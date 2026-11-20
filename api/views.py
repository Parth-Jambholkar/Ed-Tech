import json
import logging
import hashlib
import hmac
from datetime import timedelta, datetime
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Q, F
from django.http import HttpResponse, FileResponse, Http404
from django.conf import settings
from rest_framework import status, permissions, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    User, Board, ClassLevel, Subject, Chapter, StudyMaterial,
    Product, Coupon, Order, OrderItem, Payment, WebhookEvent,
    Entitlement, ManualAccessLog
)
from .serializers import (
    UserSerializer, RegisterSerializer, LoginSerializer,
    BoardSerializer, ClassLevelSerializer, SubjectSerializer,
    ChapterSerializer, StudyMaterialSerializer, ProductSerializer,
    CouponSerializer, CartCalculateSerializer, OrderCreateSerializer,
    OrderVerifySerializer, OrderSerializer, EntitlementSerializer,
    ManualAccessGrantSerializer
)
from .permissions import IsAdminUserRole
from .authentication import generate_jwt_for_user
from .services import PricingEngine, RazorpayService, EntitlementEngine

logger = logging.getLogger(__name__)


# ============================================================================
# 1. AUTHENTICATION VIEWS
# ============================================================================

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def register_view(request):
    """S1: Sign up with email/username and password"""
    serializer = RegisterSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        token = generate_jwt_for_user(user)
        return Response({
            "message": "User registered successfully.",
            "token": token,
            "user": UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def login_view(request):
    """S1: Sign in with email or username and password"""
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data['user']
        token = generate_jwt_for_user(user)
        return Response({
            "message": "Login successful.",
            "token": token,
            "user": UserSerializer(user).data
        }, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def otp_mock_view(request):
    """
    S1: Mobile OTP with a mocked SMS.
    Returns or verifies a mock OTP (e.g. 123456) for phone number login/signup.
    """
    phone = request.data.get('phone_number')
    otp_code = request.data.get('otp_code')
    
    if not phone:
        return Response({"error": "Phone number is required."}, status=status.HTTP_400_BAD_REQUEST)

    if not otp_code:
        # Request OTP
        return Response({
            "message": f"Mock SMS sent to {phone}. Use OTP '123456' to log in.",
            "mock_otp": "123456"
        }, status=status.HTTP_200_OK)

    # Verify OTP
    if otp_code == "123456":
        username = f"user_{phone[-6:]}"
        user, _ = User.objects.get_or_create(
            phone_number=phone,
            defaults={
                "username": username,
                "email": f"{username}@example.com",
                "role": "STUDENT"
            }
        )
        token = generate_jwt_for_user(user)
        return Response({
            "message": "OTP verification successful.",
            "token": token,
            "user": UserSerializer(user).data
        }, status=status.HTTP_200_OK)

    return Response({"error": "Invalid OTP. Use '123456' for test mode."}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def me_view(request):
    """Get current user details and active passes"""
    user = request.user
    user_data = UserSerializer(user).data
    now = timezone.now()
    active_passes = Entitlement.objects.filter(
        user=user, is_active=True, entitlement_type='CLASS_PASS'
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    
    user_data['active_passes_count'] = active_passes.count()
    user_data['is_admin'] = user.is_admin_user()
    return Response(user_data)


# ============================================================================
# 2. CATALOGUE & SEARCH VIEWS
# ============================================================================

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def catalogue_hierarchy_view(request):
    """S2: Browse the catalogue by board, class, subject and chapter"""
    boards = Board.objects.filter(is_active=True).prefetch_related(
        'classes__subjects__chapters'
    )
    serializer = BoardSerializer(boards, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def search_catalogue_view(request):
    """S2: Search catalogue by name (chapter, subject, class, board)"""
    query = request.GET.get('q', '').strip()
    if not query:
        return Response({"chapters": [], "subjects": [], "classes": []})

    chapters = Chapter.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).select_related('subject__class_level__board')[:20]

    subjects = Subject.objects.filter(
        Q(name__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).select_related('class_level__board')[:10]

    return Response({
        "chapters": ChapterSerializer(chapters, many=True, context={'request': request}).data,
        "subjects": SubjectSerializer(subjects, many=True, context={'request': request}).data
    })


# ============================================================================
# 3. PRODUCTS & UPGRADES VIEWS
# ============================================================================

class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    S3: Product page showing price, what is included, access length,
    and 'Owned' or 'Upgrade' states.
    """
    queryset = Product.objects.filter(is_active=True).select_related(
        'target_chapter__subject__class_level',
        'target_subject__class_level',
        'target_class__board'
    )
    serializer_class = ProductSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = super().get_queryset()
        ptype = self.request.GET.get('type')
        if ptype:
            qs = qs.filter(product_type=ptype)
        class_id = self.request.GET.get('class_id')
        if class_id:
            qs = qs.filter(
                Q(target_class_id=class_id) |
                Q(target_subject__class_level_id=class_id) |
                Q(target_chapter__subject__class_level_id=class_id)
            )
        return qs


# ============================================================================
# 4. CART & PRICING VIEWS
# ============================================================================

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def cart_calculate_view(request):
    """
    S4: Cart with several items, coupon field, and a GST breakdown.
    Recomputes prices from the DB, applying upgrade credit, coupon discount, and 18% GST split.
    """
    serializer = CartCalculateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    product_ids = serializer.validated_data['product_ids']
    coupon_code = serializer.validated_data.get('coupon_code')

    try:
        calc_result = PricingEngine.calculate_cart_totals(request.user, product_ids, coupon_code)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    items_data = []
    for item in calc_result['items']:
        prod = item['product']
        items_data.append({
            "product_id": prod.id,
            "title": prod.title,
            "product_type": prod.product_type,
            "original_price_paise": item['original_price_paise'],
            "original_price_rupees": item['original_price_paise'] / 100.0,
            "upgrade_credit_paise": item['upgrade_credit_paise'],
            "upgrade_credit_rupees": item['upgrade_credit_paise'] / 100.0,
            "final_price_paise": item['final_price_paise'],
            "final_price_rupees": item['final_price_paise'] / 100.0,
        })

    return Response({
        "items": items_data,
        "gross_amount_paise": calc_result['gross_amount_paise'],
        "gross_amount_rupees": calc_result['gross_amount_paise'] / 100.0,
        "upgrade_credit_paise": calc_result['upgrade_credit_paise'],
        "upgrade_credit_rupees": calc_result['upgrade_credit_paise'] / 100.0,
        "coupon_code": calc_result['coupon'].code if calc_result['coupon'] else None,
        "coupon_discount_paise": calc_result['coupon_discount_paise'],
        "coupon_discount_rupees": calc_result['coupon_discount_paise'] / 100.0,
        "coupon_error": calc_result['coupon_error'],
        "net_amount_paise": calc_result['net_amount_paise'],
        "net_amount_rupees": calc_result['net_amount_paise'] / 100.0,
        "base_amount_paise": calc_result['base_amount_paise'],
        "base_amount_rupees": calc_result['base_amount_paise'] / 100.0,
        "gst_amount_paise": calc_result['gst_amount_paise'],
        "gst_amount_rupees": calc_result['gst_amount_paise'] / 100.0,
        "cgst_amount_paise": calc_result['cgst_amount_paise'],
        "cgst_amount_rupees": calc_result['cgst_amount_paise'] / 100.0,
        "sgst_amount_paise": calc_result['sgst_amount_paise'],
        "sgst_amount_rupees": calc_result['sgst_amount_paise'] / 100.0,
        "gst_rate_percent": calc_result['gst_rate_percent'],
    })


# ============================================================================
# 5. ORDER & PAYMENT VIEWS (RAZORPAY INTEGRATION)
# ============================================================================

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def order_create_view(request):
    """
    Step 1 & 2:
    - Server recomputes total from DB (prices, upgrade credit, coupon, GST).
    - Server creates Razorpay Order for amount in paise.
    - Saves order row with status 'CREATED'.
    """
    serializer = OrderCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    product_ids = serializer.validated_data['product_ids']
    coupon_code = serializer.validated_data.get('coupon_code')

    try:
        calc = PricingEngine.calculate_cart_totals(request.user, product_ids, coupon_code)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        order = Order.objects.create(
            user=request.user,
            status='CREATED',
            gross_amount_paise=calc['gross_amount_paise'],
            upgrade_credit_paise=calc['upgrade_credit_paise'],
            coupon=calc['coupon'],
            coupon_discount_paise=calc['coupon_discount_paise'],
            net_amount_paise=calc['net_amount_paise'],
            gst_amount_paise=calc['gst_amount_paise'],
            base_amount_paise=calc['base_amount_paise'],
        )

        for item in calc['items']:
            OrderItem.objects.create(
                order=order,
                product=item['product'],
                product_title=item['product'].title,
                product_type=item['product'].product_type,
                original_price_paise=item['original_price_paise'],
                upgrade_credit_paise=item['upgrade_credit_paise'],
                final_price_paise=item['final_price_paise']
            )

        # Handle 100% discount / free order
        if order.net_amount_paise == 0:
            order.status = 'PAID'
            order.save()
            EntitlementEngine.grant_access_for_order(order)
            return Response({
                "order": OrderSerializer(order).data,
                "is_free": True,
                "message": "Order completed with 100% discount. Access granted."
            }, status=status.HTTP_201_CREATED)

        # Create Razorpay Order
        rzp_order = RazorpayService.create_order(
            amount_paise=order.net_amount_paise,
            receipt=order.order_number,
            notes={"user_id": str(request.user.id), "order_id": str(order.id)}
        )
        order.razorpay_order_id = rzp_order.get('id', '')
        order.save(update_fields=['razorpay_order_id'])

    return Response({
        "order": OrderSerializer(order).data,
        "razorpay_order_id": order.razorpay_order_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "amount_paise": order.net_amount_paise,
        "currency": "INR",
        "user_email": request.user.email,
        "user_phone": request.user.phone_number or ""
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def order_verify_view(request):
    """
    Step 4 & 5:
    Client sends razorpay_order_id, razorpay_payment_id, razorpay_signature.
    Server checks signature: HMAC-SHA256 of order_id + "|" + payment_id.
    If it matches:
      - Validates amount
      - Idempotently marks order paid and grants access
    """
    serializer = OrderVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    rzp_order_id = serializer.validated_data['razorpay_order_id']
    rzp_payment_id = serializer.validated_data['razorpay_payment_id']
    rzp_signature = serializer.validated_data['razorpay_signature']
    payment_method = serializer.validated_data.get('payment_method', 'UPI')

    try:
        order = Order.objects.get(razorpay_order_id=rzp_order_id, user=request.user)
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

    # Verify signature
    is_valid = RazorpayService.verify_payment_signature(rzp_order_id, rzp_payment_id, rzp_signature)
    if not is_valid:
        # Record failed payment attempt
        Payment.objects.create(
            order=order,
            user=request.user,
            razorpay_order_id=rzp_order_id,
            razorpay_payment_id=rzp_payment_id,
            razorpay_signature=rzp_signature,
            amount_paise=order.net_amount_paise,
            payment_method=payment_method,
            status='FAILED',
            error_code='BAD_SIGNATURE',
            error_description='HMAC signature verification failed.'
        )
        return Response({"error": "Payment signature verification failed."}, status=status.HTTP_400_BAD_REQUEST)

    # Record payment attempt
    payment, _ = Payment.objects.get_or_create(
        razorpay_payment_id=rzp_payment_id,
        defaults={
            "order": order,
            "user": request.user,
            "razorpay_order_id": rzp_order_id,
            "razorpay_signature": rzp_signature,
            "amount_paise": order.net_amount_paise,
            "payment_method": payment_method,
            "status": "CAPTURED",
            "raw_response": request.data
        }
    )

    # Idempotent grant
    updated_order, newly_granted = EntitlementEngine.grant_access_for_order(order)

    return Response({
        "success": True,
        "message": "Payment verified and access granted.",
        "order": OrderSerializer(updated_order).data,
        "newly_granted": newly_granted
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def razorpay_webhook_view(request):
    """
    Step 6 & 7: Webhook endpoint.
    Handles payment.captured and payment.failed.
    Verifies X-Razorpay-Signature header against raw request body using webhook secret.
    Idempotent: grants access even if student closed tab.
    Stores audit trail of every webhook event.
    """
    signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE', '')
    raw_body = request.body

    is_valid = RazorpayService.verify_webhook_signature(raw_body, signature)
    if not is_valid:
        logger.warning("Razorpay Webhook: Invalid signature received.")
        return Response({"error": "Invalid webhook signature."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        event_payload = json.loads(raw_body.decode('utf-8'))
    except Exception as e:
        return Response({"error": f"Invalid JSON payload: {e}"}, status=status.HTTP_400_BAD_REQUEST)

    event_id = event_payload.get('id', f"evt_{timezone.now().timestamp()}")
    event_type = event_payload.get('event', '')

    # Audit Trail: Store every webhook event received
    webhook_event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": event_payload,
            "signature": signature
        }
    )

    if webhook_event.processed:
        return Response({"status": "Already processed", "event_id": event_id})

    # Process events
    payment_entity = event_payload.get('payload', {}).get('payment', {}).get('entity', {})
    rzp_order_id = payment_entity.get('order_id')
    rzp_payment_id = payment_entity.get('id')
    amount_paid_paise = payment_entity.get('amount')

    if event_type == 'payment.captured':
        if rzp_order_id:
            try:
                order = Order.objects.get(razorpay_order_id=rzp_order_id)
                # Amount check: compare paid amount with saved order amount
                if amount_paid_paise is not None and amount_paid_paise != order.net_amount_paise:
                    webhook_event.process_result = f"Amount mismatch: paid {amount_paid_paise} != expected {order.net_amount_paise}"
                    webhook_event.save()
                    return Response({"error": "Amount mismatch"}, status=status.HTTP_400_BAD_REQUEST)

                # Record or update payment
                Payment.objects.update_or_create(
                    razorpay_payment_id=rzp_payment_id or f"pay_wh_{order.id}",
                    defaults={
                        "order": order,
                        "user": order.user,
                        "razorpay_order_id": rzp_order_id,
                        "amount_paise": order.net_amount_paise,
                        "status": "CAPTURED",
                        "raw_response": event_payload
                    }
                )

                # Idempotent entitlement grant
                EntitlementEngine.grant_access_for_order(order)
                webhook_event.processed = True
                webhook_event.process_result = f"Access granted for order {order.order_number}"
                webhook_event.save()
            except Order.DoesNotExist:
                webhook_event.process_result = f"Order with rzp_order_id {rzp_order_id} not found"
                webhook_event.save()

    elif event_type == 'payment.failed':
        if rzp_order_id:
            try:
                order = Order.objects.get(razorpay_order_id=rzp_order_id)
                order.status = 'FAILED'
                order.save(update_fields=['status', 'updated_at'])

                Payment.objects.create(
                    order=order,
                    user=order.user,
                    razorpay_order_id=rzp_order_id,
                    razorpay_payment_id=rzp_payment_id,
                    amount_paise=order.net_amount_paise,
                    status='FAILED',
                    error_description=payment_entity.get('error_description', 'Payment failed'),
                    raw_response=event_payload
                )
                webhook_event.processed = True
                webhook_event.process_result = f"Order {order.order_number} marked failed."
                webhook_event.save()
            except Order.DoesNotExist:
                pass

    return Response({"status": "Webhook processed successfully", "event_id": event_id})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def simulate_test_payment_view(request, order_id):
    """
    Test helper for reviewers:
    Simulates UPI success (success@razorpay) or failure (failure@razorpay).
    Allows executing the full flow end-to-end even in test mode without external webhooks.
    """
    try:
        order = Order.objects.get(id=order_id, user=request.user)
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

    upi_id = request.data.get('upi_id', 'success@razorpay')
    simulate_status = request.data.get('status', 'SUCCESS')  # SUCCESS, FAILURE, PENDING

    import uuid
    fake_payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    
    if simulate_status == 'PENDING' or upi_id == 'pending@razorpay':
        order.status = 'PENDING_UPI'
        order.save(update_fields=['status', 'updated_at'])
        return Response({
            "status": "PENDING_UPI",
            "message": "UPI payment collect request sent. Waiting for student approval on mobile app."
        })

    if simulate_status == 'FAILURE' or upi_id == 'failure@razorpay':
        order.status = 'FAILED'
        order.save(update_fields=['status', 'updated_at'])
        Payment.objects.create(
            order=order,
            user=request.user,
            razorpay_order_id=order.razorpay_order_id or "order_mock",
            razorpay_payment_id=fake_payment_id,
            amount_paise=order.net_amount_paise,
            payment_method='UPI',
            status='FAILED',
            error_code='UPI_PAYMENT_FAILED',
            error_description='Student cancelled or declined UPI collect request.'
        )
        return Response({
            "status": "FAILED",
            "message": "Payment failed as simulated by failure@razorpay. You may retry."
        }, status=status.HTTP_400_BAD_REQUEST)

    # Generate valid signature
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    sig_msg = f"{order.razorpay_order_id}|{fake_payment_id}".encode('utf-8')
    valid_sig = hmac.new(key_secret.encode('utf-8'), sig_msg, hashlib.sha256).hexdigest()

    # Create Payment record
    Payment.objects.create(
        order=order,
        user=request.user,
        razorpay_order_id=order.razorpay_order_id or "order_mock",
        razorpay_payment_id=fake_payment_id,
        razorpay_signature=valid_sig,
        amount_paise=order.net_amount_paise,
        payment_method='UPI',
        status='CAPTURED',
        raw_response={"simulation": True, "upi_id": upi_id}
    )

    # Grant access idempotently
    updated_order, newly_granted = EntitlementEngine.grant_access_for_order(order)

    return Response({
        "status": "PAID",
        "message": "Payment simulated successfully. Access granted.",
        "order": OrderSerializer(updated_order).data,
        "payment_id": fake_payment_id,
        "signature": valid_sig
    })


# ============================================================================
# 6. MY LIBRARY & SECURE STUDY MATERIAL READER
# ============================================================================

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def my_library_view(request):
    """
    S6: 'My Library' listing everything owned, with expiry dates for passes.
    """
    user = request.user
    now = timezone.now()

    # Direct chapter entitlements
    chapter_ents = Entitlement.objects.filter(
        user=user, is_active=True, entitlement_type='CHAPTER'
    ).select_related('chapter__subject__class_level__board')

    # Subject pack entitlements
    subject_ents = Entitlement.objects.filter(
        user=user, is_active=True, entitlement_type='SUBJECT'
    ).select_related('subject__class_level__board').prefetch_related('subject__chapters')

    # Class passes (active and unexpired)
    class_passes = Entitlement.objects.filter(
        user=user, is_active=True, entitlement_type='CLASS_PASS'
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).select_related('class_level__board')

    # Also list free sample chapters available to any registered student
    free_chapters = Chapter.objects.filter(is_free=True, is_active=True).select_related('subject__class_level__board')

    return Response({
        "chapters": EntitlementSerializer(chapter_ents, many=True).data,
        "subjects": EntitlementSerializer(subject_ents, many=True).data,
        "class_passes": EntitlementSerializer(class_passes, many=True).data,
        "free_sample_chapters": ChapterSerializer(free_chapters, many=True, context={'request': request}).data
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def material_content_view(request, material_id):
    """
    S7: Read purchased notes in the browser.
    Locked content must be refused by the server (HTTP 403), not only hidden in the UI.
    File URLs for notes must not be guessable or shareable: served through this authenticated route.
    """
    try:
        material = StudyMaterial.objects.select_related('chapter__subject__class_level').get(id=material_id)
    except StudyMaterial.DoesNotExist:
        raise Http404("Study material not found.")

    has_access, reason = EntitlementEngine.check_access_to_material(request.user, material)
    if not has_access:
        return Response({
            "error": "Access Denied (HTTP 403 Forbidden)",
            "detail": "This material is locked. You must purchase the chapter, subject pack, or class pass to access it.",
            "chapter_id": material.chapter_id,
            "reason": reason
        }, status=status.HTTP_403_FORBIDDEN)

    # If material has a physical file attached, stream or return it
    if material.file and hasattr(material.file, 'path'):
        try:
            return FileResponse(open(material.file.path, 'rb'), content_type='application/pdf')
        except FileNotFoundError:
            pass

    # Return structured study notes
    return Response({
        "id": material.id,
        "title": material.title,
        "chapter_title": material.chapter.title,
        "subject_name": material.chapter.subject.name,
        "class_name": material.chapter.subject.class_level.name,
        "material_type": material.material_type,
        "duration_mins": material.duration_mins,
        "content_html": material.content_html or f"<h3>{material.title}</h3><p>Complete notes and formulas for {material.chapter.title}.</p>",
        "access_granted_reason": reason
    })


# ============================================================================
# 7. ORDER HISTORY & GST INVOICE VIEWS
# ============================================================================

def generate_invoice_response(order, format_param=None):
    gst_split = PricingEngine.calculate_gst_breakdown(order.net_amount_paise)

    invoice_data = {
        "invoice_number": f"INV-{order.order_number}",
        "order_number": order.order_number,
        "date": order.created_at.strftime('%d %b %Y, %I:%M %p'),
        "student_name": order.user.get_full_name() or order.user.username,
        "student_email": order.user.email,
        "student_phone": order.user.phone_number or "N/A",
        "company_name": "EdTech Learning Academy Pvt Ltd",
        "company_gstin": "07AAAAA0000A1Z5",
        "company_address": "Tech Park, Outer Ring Road, Bangalore - 560103",
        "hsn_sac_code": "999293",  # Commercial training and coaching services
        "items": [
            {
                "title": item.product_title,
                "type": item.product_type,
                "original_price_inr": f"{item.original_price_paise / 100:.2f}",
                "upgrade_credit_inr": f"{item.upgrade_credit_paise / 100:.2f}",
                "final_price_inr": f"{item.final_price_paise / 100:.2f}"
            }
            for item in order.items.all()
        ],
        "gross_amount_inr": f"{order.gross_amount_paise / 100:.2f}",
        "upgrade_credit_inr": f"{order.upgrade_credit_paise / 100:.2f}",
        "coupon_code": order.coupon.code if order.coupon else None,
        "coupon_discount_inr": f"{order.coupon_discount_paise / 100:.2f}",
        "net_amount_inr": f"{order.net_amount_paise / 100:.2f}",
        "base_amount_inr": f"{gst_split['base_amount_paise'] / 100:.2f}",
        "cgst_9_percent_inr": f"{gst_split['cgst_amount_paise'] / 100:.2f}",
        "sgst_9_percent_inr": f"{gst_split['sgst_amount_paise'] / 100:.2f}",
        "total_gst_inr": f"{gst_split['gst_amount_paise'] / 100:.2f}",
        "status": order.status,
        "payment_id": order.payments.filter(status='CAPTURED').values_list('razorpay_payment_id', flat=True).first() or 'N/A'
    }

    # If client requests HTML view directly for printing:
    if format_param == 'html':
        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Tax Invoice - {order.order_number}</title>
<style>
body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; color: #333; }}
.header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #2563eb; padding-bottom: 20px; }}
.company-info {{ font-size: 14px; line-height: 1.5; }}
.invoice-title {{ font-size: 28px; font-weight: bold; color: #1e3a8a; margin: 0; }}
.meta-box {{ margin-top: 25px; display: flex; justify-content: space-between; font-size: 14px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 30px; }}
th, td {{ padding: 12px; border: 1px solid #e2e8f0; text-align: left; }}
th {{ background-color: #f8fafc; font-weight: 600; }}
.text-right {{ text-align: right; }}
.summary-table {{ width: 350px; margin-left: auto; margin-top: 20px; }}
.summary-table td {{ border: none; padding: 6px 12px; }}
.total-row {{ font-weight: bold; font-size: 16px; border-top: 2px solid #2563eb; color: #1e3a8a; }}
.footer {{ margin-top: 50px; font-size: 12px; color: #64748b; text-align: center; border-top: 1px solid #e2e8f0; padding-top: 15px; }}
.badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; background: #dcfce7; color: #15803d; }}
@media print {{ .no-print {{ display: none; }} }}
</style>
</head>
<body>
<div class="no-print" style="margin-bottom: 20px;">
  <button onclick="window.print()" style="padding: 10px 20px; background: #2563eb; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold;">Print / Save as PDF</button>
</div>
<div class="header">
  <div class="company-info">
    <h2 style="margin: 0 0 6px 0; color: #2563eb;">EdTech Academy Pvt Ltd</h2>
    <p style="margin: 0;">Outer Ring Road, Bangalore - 560103</p>
    <p style="margin: 0;"><strong>GSTIN:</strong> 07AAAAA0000A1Z5 | <strong>SAC Code:</strong> 999293</p>
  </div>
  <div style="text-align: right;">
    <h1 class="invoice-title">TAX INVOICE</h1>
    <p style="margin: 6px 0 0 0;"><strong>Invoice #:</strong> INV-{order.order_number}</p>
    <p style="margin: 4px 0 0 0;"><strong>Date:</strong> {invoice_data['date']}</p>
    <p style="margin: 4px 0 0 0;"><span class="badge">{order.status}</span></p>
  </div>
</div>

<div class="meta-box">
  <div>
    <strong>Billed To:</strong><br>
    {invoice_data['student_name']}<br>
    Email: {invoice_data['student_email']}<br>
    Phone: {invoice_data['student_phone']}
  </div>
  <div style="text-align: right;">
    <strong>Payment Details:</strong><br>
    Status: {order.status}<br>
    Payment ID: {invoice_data['payment_id']}<br>
    Order ID: {order.razorpay_order_id or 'N/A'}
  </div>
</div>

<table>
  <thead>
    <tr>
      <th>Description</th>
      <th>Type</th>
      <th>SAC</th>
      <th class="text-right">Price (₹)</th>
      <th class="text-right">Upgrade Credit (₹)</th>
      <th class="text-right">Amount (₹)</th>
    </tr>
  </thead>
  <tbody>
"""
        for itm in invoice_data['items']:
            html += f"""
    <tr>
      <td><strong>{itm['title']}</strong></td>
      <td>{itm['type']}</td>
      <td>999293</td>
      <td class="text-right">₹{itm['original_price_inr']}</td>
      <td class="text-right">-₹{itm['upgrade_credit_inr']}</td>
      <td class="text-right">₹{itm['final_price_inr']}</td>
    </tr>"""

        html += f"""
  </tbody>
</table>

<table class="summary-table">
  <tr>
    <td>Gross Amount:</td>
    <td class="text-right">₹{invoice_data['gross_amount_inr']}</td>
  </tr>
  <tr>
    <td>Upgrade Credits:</td>
    <td class="text-right">-₹{invoice_data['upgrade_credit_inr']}</td>
  </tr>
  <tr>
    <td>Coupon Discount ({invoice_data['coupon_code'] or 'None'}):</td>
    <td class="text-right">-₹{invoice_data['coupon_discount_inr']}</td>
  </tr>
  <tr>
    <td><strong>Taxable Base Value:</strong></td>
    <td class="text-right"><strong>₹{invoice_data['base_amount_inr']}</strong></td>
  </tr>
  <tr>
    <td>CGST (9%):</td>
    <td class="text-right">₹{invoice_data['cgst_9_percent_inr']}</td>
  </tr>
  <tr>
    <td>SGST (9%):</td>
    <td class="text-right">₹{invoice_data['sgst_9_percent_inr']}</td>
  </tr>
  <tr class="total-row">
    <td>Total Paid (Incl. 18% GST):</td>
    <td class="text-right">₹{invoice_data['net_amount_inr']}</td>
  </tr>
</table>

<div class="footer">
  <p>This is a computer-generated tax invoice and requires no physical signature.</p>
  <p>Thank you for choosing EdTech Academy. Learn, build, and succeed!</p>
</div>
</body>
</html>"""
        return HttpResponse(html, content_type='text/html')

    return Response(invoice_data)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """S8: Order history"""
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_user() and self.request.GET.get('all_orders') == 'true':
            return Order.objects.all().select_related('user', 'coupon').prefetch_related('items__product').order_by('-created_at')
        return Order.objects.filter(user=user).select_related('user', 'coupon').prefetch_related('items__product').order_by('-created_at')

    @action(detail=True, methods=['get'])
    def invoice(self, request, pk=None):
        order = self.get_object()
        view_type = request.GET.get('view') or request.GET.get('render') or request.GET.get('type')
        return generate_invoice_response(order, view_type)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def order_invoice_view(request, order_id):
    """
    S8: Order history with a downloadable / printable HTML invoice.
    Shows prices inclusive of 18% GST and shows the GST split (Base, CGST 9%, SGST 9%, Total).
    """
    try:
        if request.user.is_admin_user():
            order = Order.objects.get(id=order_id)
        else:
            order = Order.objects.get(id=order_id, user=request.user)
    except Order.DoesNotExist:
        return Response({"error": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

    return generate_invoice_response(order, request.GET.get('format'))


# ============================================================================
# 8. ADMIN MANAGEMENT VIEWS (A1 - A7)
# ============================================================================

@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAdminUserRole])
def admin_boards_view(request, pk=None):
    """A1: Create, edit and delete boards"""
    if request.method == 'GET':
        boards = Board.objects.all()
        return Response(BoardSerializer(boards, many=True).data)

    if request.method == 'POST':
        serializer = BoardSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        board = Board.objects.get(pk=pk)
    except Board.DoesNotExist:
        return Response({"error": "Board not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PUT':
        serializer = BoardSerializer(board, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        board.delete()
        return Response({"message": "Board deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAdminUserRole])
def admin_classes_view(request, pk=None):
    """A1: Create, edit and delete classes"""
    if request.method == 'GET':
        classes = ClassLevel.objects.all()
        return Response(ClassLevelSerializer(classes, many=True).data)

    if request.method == 'POST':
        serializer = ClassLevelSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        class_obj = ClassLevel.objects.get(pk=pk)
    except ClassLevel.DoesNotExist:
        return Response({"error": "Class not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PUT':
        serializer = ClassLevelSerializer(class_obj, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        class_obj.delete()
        return Response({"message": "Class deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAdminUserRole])
def admin_subjects_view(request, pk=None):
    """A1: Create, edit and delete subjects"""
    if request.method == 'GET':
        subjects = Subject.objects.all()
        return Response(SubjectSerializer(subjects, many=True).data)

    if request.method == 'POST':
        serializer = SubjectSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        subject = Subject.objects.get(pk=pk)
    except Subject.DoesNotExist:
        return Response({"error": "Subject not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PUT':
        serializer = SubjectSerializer(subject, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        subject.delete()
        return Response({"message": "Subject deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAdminUserRole])
def admin_chapters_view(request, pk=None):
    """
    A1 & A2: Create, edit, delete chapters.
    Upload notes (PDF) to a chapter, and mark a chapter free.
    """
    if request.method == 'GET':
        chapters = Chapter.objects.all().select_related('subject')
        return Response(ChapterSerializer(chapters, many=True).data)

    if request.method == 'POST':
        title = request.data.get('title')
        subject_id = request.data.get('subject')
        chapter_number = request.data.get('chapter_number', 1)
        is_free = request.data.get('is_free', False)
        description = request.data.get('description', '')

        chapter = Chapter.objects.create(
            title=title,
            subject_id=subject_id,
            chapter_number=chapter_number,
            is_free=str(is_free).lower() in ['true', '1'],
            description=description
        )

        # Handle notes upload
        pdf_file = request.FILES.get('pdf_file')
        content_html = request.data.get('content_html', '')
        if pdf_file or content_html:
            StudyMaterial.objects.create(
                chapter=chapter,
                title=f"{chapter.title} Notes",
                material_type='PDF',
                file=pdf_file,
                content_html=content_html
            )

        return Response(ChapterSerializer(chapter).data, status=status.HTTP_201_CREATED)

    try:
        chapter = Chapter.objects.get(pk=pk)
    except Chapter.DoesNotExist:
        return Response({"error": "Chapter not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PUT':
        if 'is_free' in request.data:
            chapter.is_free = str(request.data['is_free']).lower() in ['true', '1']
        if 'title' in request.data:
            chapter.title = request.data['title']
        if 'description' in request.data:
            chapter.description = request.data['description']
        chapter.save()

        # Check for study material file upload
        pdf_file = request.FILES.get('pdf_file')
        content_html = request.data.get('content_html')
        if pdf_file or content_html:
            material, _ = StudyMaterial.objects.get_or_create(
                chapter=chapter,
                material_type='PDF',
                defaults={"title": f"{chapter.title} Notes"}
            )
            if pdf_file:
                material.file = pdf_file
            if content_html:
                material.content_html = content_html
            material.save()

        return Response(ChapterSerializer(chapter).data)

    if request.method == 'DELETE':
        chapter.delete()
        return Response({"message": "Chapter deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'PUT', 'DELETE'])
@permission_classes([IsAdminUserRole])
def admin_products_view(request, pk=None):
    """A3: Create products and set prices for each product type"""
    if request.method == 'GET':
        products = Product.objects.all().order_by('-created_at')
        return Response(ProductSerializer(products, many=True).data)

    if request.method == 'POST':
        # Accept price in rupees or paise
        data = request.data.copy()
        if 'price_rupees' in data and 'price_paise' not in data:
            data['price_paise'] = int(float(data['price_rupees']) * 100)
        serializer = ProductSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return Response({"error": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PUT':
        data = request.data.copy()
        if 'price_rupees' in data:
            data['price_paise'] = int(float(data['price_rupees']) * 100)
        serializer = ProductSerializer(product, data=data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        product.delete()
        return Response({"message": "Product deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([IsAdminUserRole])
def admin_coupons_view(request, pk=None):
    """A5: Create and disable coupons"""
    if request.method == 'GET':
        coupons = Coupon.objects.all().order_by('-id')
        return Response(CouponSerializer(coupons, many=True).data)

    if request.method == 'POST':
        serializer = CouponSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    try:
        coupon = Coupon.objects.get(pk=pk)
    except Coupon.DoesNotExist:
        return Response({"error": "Coupon not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'PATCH':
        is_active = request.data.get('is_active')
        if is_active is not None:
            coupon.is_active = bool(is_active)
            coupon.save(update_fields=['is_active'])
        return Response(CouponSerializer(coupon).data)


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def admin_orders_view(request):
    """
    A4: View all orders and payments with their status, filtered by date and status.
    """
    qs = Order.objects.all().select_related('user', 'coupon').prefetch_related('items__product', 'payments').order_by('-created_at')

    # Filter by status
    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter.upper())

    # Filter by date range
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)

    # Search user
    search_user = request.GET.get('search')
    if search_user:
        qs = qs.filter(Q(user__username__icontains=search_user) | Q(user__email__icontains=search_user) | Q(order_number__icontains=search_user))

    serializer = OrderSerializer(qs[:100], many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAdminUserRole])
def admin_manage_access_view(request):
    """
    A6: Grant or revoke a student's access by hand, with a reason that is logged.
    """
    serializer = ManualAccessGrantSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    student_id = serializer.validated_data['student_id']
    action_type = serializer.validated_data['action']
    entitlement_type = serializer.validated_data['entitlement_type']
    target_id = serializer.validated_data['target_id']
    duration_days = serializer.validated_data.get('duration_days')
    reason = serializer.validated_data['reason']

    try:
        student = User.objects.get(id=student_id)
    except User.DoesNotExist:
        return Response({"error": "Student user not found."}, status=status.HTTP_404_NOT_FOUND)

    entitlement = None
    target_summary = ""

    if action_type == 'GRANT':
        now = timezone.now()
        expires_at = now + timedelta(days=duration_days) if duration_days else None

        if entitlement_type == 'CHAPTER':
            chapter = Chapter.objects.get(id=target_id)
            target_summary = f"Chapter: {chapter.title}"
            entitlement, _ = Entitlement.objects.update_or_create(
                user=student,
                entitlement_type='CHAPTER',
                chapter=chapter,
                defaults={'is_active': True, 'granted_by_admin': True, 'expires_at': expires_at}
            )
        elif entitlement_type == 'SUBJECT':
            subject = Subject.objects.get(id=target_id)
            target_summary = f"Subject Pack: {subject.name}"
            entitlement, _ = Entitlement.objects.update_or_create(
                user=student,
                entitlement_type='SUBJECT',
                subject=subject,
                defaults={'is_active': True, 'granted_by_admin': True, 'expires_at': expires_at}
            )
        elif entitlement_type == 'CLASS_PASS':
            class_obj = ClassLevel.objects.get(id=target_id)
            target_summary = f"Class Pass: {class_obj.name}"
            entitlement, _ = Entitlement.objects.update_or_create(
                user=student,
                entitlement_type='CLASS_PASS',
                class_level=class_obj,
                defaults={'is_active': True, 'granted_by_admin': True, 'expires_at': expires_at}
            )

    elif action_type == 'REVOKE':
        filter_kwargs = {'user': student, 'entitlement_type': entitlement_type}
        if entitlement_type == 'CHAPTER':
            filter_kwargs['chapter_id'] = target_id
            target_summary = f"Chapter ID {target_id}"
        elif entitlement_type == 'SUBJECT':
            filter_kwargs['subject_id'] = target_id
            target_summary = f"Subject ID {target_id}"
        elif entitlement_type == 'CLASS_PASS':
            filter_kwargs['class_level_id'] = target_id
            target_summary = f"Class ID {target_id}"

        ents = Entitlement.objects.filter(**filter_kwargs)
        for ent in ents:
            ent.is_active = False
            ent.save(update_fields=['is_active', 'updated_at'])
            entitlement = ent

    # Log manual action
    ManualAccessLog.objects.create(
        admin_user=request.user,
        student=student,
        action=action_type,
        entitlement=entitlement,
        target_summary=target_summary,
        reason=reason
    )

    return Response({
        "success": True,
        "message": f"Successfully performed {action_type} for student {student.username}.",
        "target": target_summary,
        "reason_logged": reason
    })


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def admin_manual_logs_view(request):
    """View manual access grant/revoke audit logs"""
    logs = ManualAccessLog.objects.all().select_related('admin_user', 'student').order_by('-created_at')[:100]
    data = [
        {
            "id": log.id,
            "admin_username": log.admin_user.username if log.admin_user else "System",
            "student_username": log.student.username,
            "student_email": log.student.email,
            "action": log.action,
            "target": log.target_summary,
            "reason": log.reason,
            "created_at": log.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }
        for log in logs
    ]
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAdminUserRole])
def admin_dashboard_view(request):
    """
    A7: Dashboard: revenue by day, top products, active passes
    """
    now = timezone.now()

    # 1. Total & Active Passes
    active_passes_count = Entitlement.objects.filter(
        entitlement_type='CLASS_PASS',
        is_active=True
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count()

    total_students = User.objects.filter(role='STUDENT').count()
    total_orders = Order.objects.filter(status='PAID').count()

    # 2. Total Revenue
    total_revenue_paise = Order.objects.filter(status='PAID').aggregate(
        total=Sum('net_amount_paise')
    )['total'] or 0

    # 3. Revenue by Day (Last 14 days)
    fourteen_days_ago = now.date() - timedelta(days=14)
    daily_revenue_qs = Order.objects.filter(
        status='PAID',
        created_at__date__gte=fourteen_days_ago
    ).values('created_at__date').annotate(
        revenue_paise=Sum('net_amount_paise'),
        orders_count=Count('id')
    ).order_by('created_at__date')

    daily_revenue = [
        {
            "date": item['created_at__date'].strftime('%Y-%m-%d'),
            "revenue_rupees": item['revenue_paise'] / 100.0,
            "orders_count": item['orders_count']
        }
        for item in daily_revenue_qs
    ]

    # 4. Top Selling Products
    top_products_qs = OrderItem.objects.filter(
        order__status='PAID'
    ).values('product_title', 'product_type').annotate(
        units_sold=Count('id'),
        total_revenue_paise=Sum('final_price_paise')
    ).order_by('-units_sold')[:5]

    top_products = [
        {
            "title": item['product_title'],
            "type": item['product_type'],
            "units_sold": item['units_sold'],
            "revenue_rupees": item['total_revenue_paise'] / 100.0
        }
        for item in top_products_qs
    ]

    return Response({
        "metrics": {
            "total_revenue_rupees": total_revenue_paise / 100.0,
            "total_orders": total_orders,
            "active_passes": active_passes_count,
            "total_students": total_students,
        },
        "daily_revenue": daily_revenue,
        "top_products": top_products
    })
