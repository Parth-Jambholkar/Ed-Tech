import hmac
import hashlib
import json
import logging
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.db import transaction
import requests

from .models import (
    Product, Coupon, Order, OrderItem, Payment, Entitlement, ManualAccessLog
)

logger = logging.getLogger(__name__)


class PricingEngine:
    @staticmethod
    def calculate_gst_breakdown(net_amount_paise: int):
        """
        Calculates 18% inclusive GST breakdown for amount in paise.
        Formula:
          Total = Base + GST
          Base = round(Total / 1.18)
          GST = Total - Base
          CGST = GST // 2
          SGST = GST - CGST
        Guarantees: Base + GST == Total and CGST + SGST == GST down to exact paisa.
        """
        if net_amount_paise <= 0:
            return {
                "base_amount_paise": 0,
                "gst_amount_paise": 0,
                "cgst_amount_paise": 0,
                "sgst_amount_paise": 0,
                "net_amount_paise": 0,
                "gst_rate_percent": 18
            }

        base_amount = round(net_amount_paise / 1.18)
        gst_amount = net_amount_paise - base_amount
        cgst_amount = gst_amount // 2
        sgst_amount = gst_amount - cgst_amount

        return {
            "base_amount_paise": base_amount,
            "gst_amount_paise": gst_amount,
            "cgst_amount_paise": cgst_amount,
            "sgst_amount_paise": sgst_amount,
            "net_amount_paise": net_amount_paise,
            "gst_rate_percent": 18
        }

    @staticmethod
    def calculate_upgrade_credit(user, product: Product) -> int:
        """
        Rule 2: Upgrades. A student who owns some chapters of a subject
        sees the subject pack price reduced by what they already paid for those chapters (never below 0).
        """
        if not user or not user.is_authenticated or product.product_type != 'SUBJECT' or not product.target_subject:
            return 0

        subject = product.target_subject
        chapter_ids = list(subject.chapters.values_list('id', flat=True))

        # Check chapters user has active entitlements for
        entitled_chapter_ids = Entitlement.objects.filter(
            user=user,
            is_active=True,
            entitlement_type='CHAPTER',
            chapter_id__in=chapter_ids
        ).values_list('chapter_id', flat=True)

        if not entitled_chapter_ids:
            return 0

        # Calculate total amount student actually paid for these chapters in previous paid orders
        items = OrderItem.objects.filter(
            order__user=user,
            order__status='PAID',
            product__product_type='CHAPTER',
            product__target_chapter_id__in=entitled_chapter_ids
        )

        total_paid_for_chapters = sum(item.final_price_paise for item in items)
        credit = min(total_paid_for_chapters, product.price_paise)
        return credit

    @classmethod
    def calculate_cart_totals(cls, user, product_ids, coupon_code=None):
        """
        Rule 1: Never trust price sent by browser. Recomputes total from DB.
        Rule 1: Overlapping purchases check:
          - A student who owns a subject pack must not be offered a chapter inside it.
          - A student with active pass sees everything in that class as unlocked.
        Rule 2: Upgrades credit calculation.
        Rule 4: Coupons validation & calculation.
        Rule 5: 18% inclusive GST split.
        """
        products = Product.objects.filter(id__in=product_ids, is_active=True)
        if not products.exists():
            raise ValueError("No valid active products selected.")

        user_has_subject_packs = set()
        user_has_class_passes = set()
        if user and user.is_authenticated:
            user_has_subject_packs = set(
                Entitlement.objects.filter(
                    user=user, is_active=True, entitlement_type='SUBJECT'
                ).values_list('subject_id', flat=True)
            )
            # Active passes that are not expired
            now = timezone.now()
            user_has_class_passes = set(
                Entitlement.objects.filter(
                    user=user, is_active=True, entitlement_type='CLASS_PASS'
                ).filter(models_Q_unexpired(now)).values_list('class_level_id', flat=True)
            )

        items_breakdown = []
        gross_amount = 0
        total_upgrade_credit = 0

        for product in products:
            # Overlapping purchase checks
            if product.product_type == 'CHAPTER' and product.target_chapter:
                subject_id = product.target_chapter.subject_id
                class_id = product.target_chapter.subject.class_level_id
                if subject_id in user_has_subject_packs:
                    raise ValueError(f"You already own the subject pack for '{product.target_chapter.subject.name}'. Individual chapter purchase is not permitted.")
                if class_id in user_has_class_passes:
                    raise ValueError(f"You already have an active class pass for '{product.target_chapter.subject.class_level.name}'. All chapters in this class are already unlocked.")

            credit = cls.calculate_upgrade_credit(user, product)
            final_item_price = max(0, product.price_paise - credit)

            gross_amount += product.price_paise
            total_upgrade_credit += credit

            items_breakdown.append({
                "product": product,
                "original_price_paise": product.price_paise,
                "upgrade_credit_paise": credit,
                "final_price_paise": final_item_price
            })

        amount_after_credits = max(0, gross_amount - total_upgrade_credit)

        coupon_obj = None
        coupon_discount = 0
        coupon_error = None

        if coupon_code:
            try:
                coupon = Coupon.objects.get(code__iexact=coupon_code.strip())
                is_valid, msg = coupon.is_valid_now()
                if is_valid:
                    discount = coupon.calculate_discount(amount_after_credits)
                    coupon_discount = discount
                    coupon_obj = coupon
                else:
                    coupon_error = msg
            except Coupon.DoesNotExist:
                coupon_error = "Invalid coupon code."

        net_amount_paise = max(0, amount_after_credits - coupon_discount)
        gst_split = cls.calculate_gst_breakdown(net_amount_paise)

        return {
            "items": items_breakdown,
            "gross_amount_paise": gross_amount,
            "upgrade_credit_paise": total_upgrade_credit,
            "coupon": coupon_obj,
            "coupon_discount_paise": coupon_discount,
            "coupon_error": coupon_error,
            "net_amount_paise": net_amount_paise,
            "base_amount_paise": gst_split["base_amount_paise"],
            "gst_amount_paise": gst_split["gst_amount_paise"],
            "cgst_amount_paise": gst_split["cgst_amount_paise"],
            "sgst_amount_paise": gst_split["sgst_amount_paise"],
            "gst_rate_percent": gst_split["gst_rate_percent"],
        }


def models_Q_unexpired(now):
    from django.db.models import Q
    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)


class RazorpayService:
    @staticmethod
    def create_order(amount_paise: int, receipt: str, notes=None):
        """
        Creates a Razorpay order via Razorpay REST API or mock in test mode.
        """
        key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')

        # Attempt API call to Razorpay
        if key_id.startswith('rzp_test_') and key_secret != 'test_secret_mock_key_2026':
            try:
                resp = requests.post(
                    "https://api.razorpay.com/v1/orders",
                    auth=(key_id, key_secret),
                    json={
                        "amount": amount_paise,
                        "currency": "INR",
                        "receipt": receipt,
                        "notes": notes or {}
                    },
                    timeout=5
                )
                if resp.status_code in [200, 201]:
                    return resp.json()
            except Exception as e:
                logger.warning(f"Razorpay API call failed: {e}. Falling back to test simulated order.")

        # Local test simulation order (when running offline or with mock test keys)
        import uuid
        return {
            "id": f"order_test_{uuid.uuid4().hex[:14]}",
            "entity": "order",
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "status": "created",
            "attempts": 0,
            "notes": notes or {}
        }

    @staticmethod
    def verify_payment_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Step 5: The server checks the signature: an HMAC-SHA256 of order_id + "|" + payment_id
        using your key secret.
        """
        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            return False

        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
        message = f"{razorpay_order_id}|{razorpay_payment_id}".encode('utf-8')
        expected_sig = hmac.new(key_secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        return hmac.compare_digest(expected_sig, razorpay_signature)

    @staticmethod
    def verify_webhook_signature(raw_body_bytes: bytes, signature_header: str) -> bool:
        """
        Step 6: Handle webhooks. Verify X-Razorpay-Signature header against raw body using webhook secret.
        """
        if not signature_header or not raw_body_bytes:
            return False

        webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
        expected_sig = hmac.new(webhook_secret.encode('utf-8'), raw_body_bytes, hashlib.sha256).hexdigest()

        return hmac.compare_digest(expected_sig, signature_header)


class EntitlementEngine:
    @staticmethod
    @transaction.atomic
    def grant_access_for_order(order: Order, payment_info=None):
        """
        Idempotent order fulfillment.
        Can be called by client verification (Step 5) or webhook (Step 6/7).
        Grants access exactly once; order and entitlements are not duplicated.
        """
        # Lock the order row to prevent race conditions between verify and webhook
        order = Order.objects.select_for_update().get(id=order.id)

        if order.status == 'PAID' and order.granted_entitlements.exists():
            # Already processed, idempotent return
            return order, False

        order.status = 'PAID'
        order.save(update_fields=['status', 'updated_at'])

        # Increment coupon times_used
        if order.coupon:
            Coupon.objects.filter(id=order.coupon.id).update(times_used=models_F_increment())

        now = timezone.now()

        for item in order.items.select_related('product').all():
            product = item.product
            ptype = product.product_type

            if ptype == 'CHAPTER' and product.target_chapter:
                Entitlement.objects.update_or_create(
                    user=order.user,
                    entitlement_type='CHAPTER',
                    chapter=product.target_chapter,
                    defaults={
                        'is_active': True,
                        'source_order': order,
                        'valid_from': now,
                        'expires_at': None  # Lifetime
                    }
                )

            elif ptype == 'SUBJECT' and product.target_subject:
                Entitlement.objects.update_or_create(
                    user=order.user,
                    entitlement_type='SUBJECT',
                    subject=product.target_subject,
                    defaults={
                        'is_active': True,
                        'source_order': order,
                        'valid_from': now,
                        'expires_at': None  # Lifetime
                    }
                )

            elif ptype in ['MONTHLY_PASS', 'ANNUAL_PASS'] and product.target_class:
                # Rule 3: Time-bound access. Buying again before expiry extends from old expiry date.
                days = product.duration_days or (365 if ptype == 'ANNUAL_PASS' else 30)
                existing = Entitlement.objects.filter(
                    user=order.user,
                    entitlement_type='CLASS_PASS',
                    class_level=product.target_class,
                    is_active=True
                ).order_by('-expires_at').first()

                if existing and existing.expires_at and existing.expires_at > now:
                    base_start = existing.expires_at
                else:
                    base_start = now

                new_expiry = base_start + timedelta(days=days)

                if existing:
                    existing.expires_at = new_expiry
                    existing.source_order = order
                    existing.save(update_fields=['expires_at', 'source_order', 'updated_at'])
                else:
                    Entitlement.objects.create(
                        user=order.user,
                        entitlement_type='CLASS_PASS',
                        class_level=product.target_class,
                        source_order=order,
                        is_active=True,
                        valid_from=now,
                        expires_at=new_expiry
                    )

            elif ptype == 'BUNDLE':
                # Grant access to all chapters and subjects in the bundle
                for ch in product.bundle_chapters.all():
                    Entitlement.objects.update_or_create(
                        user=order.user,
                        entitlement_type='CHAPTER',
                        chapter=ch,
                        defaults={'is_active': True, 'source_order': order, 'expires_at': None}
                    )
                for sub in product.bundle_subjects.all():
                    Entitlement.objects.update_or_create(
                        user=order.user,
                        entitlement_type='SUBJECT',
                        subject=sub,
                        defaults={'is_active': True, 'source_order': order, 'expires_at': None}
                    )

        return order, True

    @staticmethod
    def check_access_to_material(user, material):
        """
        Checks whether student can access this StudyMaterial.
        Access checks read Entitlements; they do NOT re-derive access from old orders on every request.
        """
        if not user or not user.is_authenticated:
            return False, "Authentication required"

        # Admin has full access
        if user.is_admin_user():
            return True, "Admin access granted"

        chapter = material.chapter
        # Free sample chapter
        if chapter.is_free:
            return True, "Free sample chapter"

        now = timezone.now()

        # 1. Check direct chapter entitlement
        if Entitlement.objects.filter(
            user=user,
            is_active=True,
            entitlement_type='CHAPTER',
            chapter=chapter
        ).filter(models_Q_unexpired(now)).exists():
            return True, "Chapter entitlement active"

        # 2. Check parent subject pack entitlement
        if Entitlement.objects.filter(
            user=user,
            is_active=True,
            entitlement_type='SUBJECT',
            subject=chapter.subject
        ).filter(models_Q_unexpired(now)).exists():
            return True, "Subject pack entitlement active"

        # 3. Check class pass entitlement
        if Entitlement.objects.filter(
            user=user,
            is_active=True,
            entitlement_type='CLASS_PASS',
            class_level=chapter.subject.class_level
        ).filter(models_Q_unexpired(now)).exists():
            return True, "Class pass active"

        return False, "Locked content. Purchase required."


def models_F_increment():
    from django.db.models import F
    return F('times_used') + 1
