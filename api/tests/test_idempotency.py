import json
import hmac
import hashlib
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from api.models import User, Board, ClassLevel, Subject, Chapter, Product, Order, OrderItem, Entitlement, WebhookEvent
from api.services import EntitlementEngine

class IdempotencyAndFulfillmentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.secret_key = "test_key_secret"
        self.webhook_secret = "test_webhook_secret"

        self.user = User.objects.create_user(username='student_idem', email='idem@test.com', password='password123')
        self.board = Board.objects.create(name='Test Board', code='TB')
        self.class10 = ClassLevel.objects.create(board=self.board, name='Class 10', grade_number=10)
        self.physics = Subject.objects.create(class_level=self.class10, name='Physics', code='PHY10')
        self.chapter = Chapter.objects.create(subject=self.physics, title='Ch 1', chapter_number=1, is_free=False)

        self.prod_ch = Product.objects.create(
            product_type='CHAPTER',
            title='Ch 1 Notes',
            target_chapter=self.chapter,
            price_paise=4900
        )
        self.prod_monthly_pass = Product.objects.create(
            product_type='MONTHLY_PASS',
            title='Class 10 Monthly Pass',
            target_class=self.class10,
            price_paise=29900,
            duration_days=30
        )

    def test_idempotent_access_grant(self):
        """Correctness rule: Step 5 and the webhook can both arrive; access must be granted exactly once"""
        order = Order.objects.create(
            user=self.user,
            status='CREATED',
            net_amount_paise=4900,
            gross_amount_paise=4900,
            razorpay_order_id='order_test_idem_1'
        )
        OrderItem.objects.create(
            order=order,
            product=self.prod_ch,
            product_title=self.prod_ch.title,
            product_type='CHAPTER',
            original_price_paise=4900,
            final_price_paise=4900
        )

        # First grant (simulating Step 5 client verification)
        updated_order_1, newly_granted_1 = EntitlementEngine.grant_access_for_order(order)
        self.assertTrue(newly_granted_1)
        self.assertEqual(updated_order_1.status, 'PAID')
        self.assertEqual(Entitlement.objects.filter(user=self.user, chapter=self.chapter).count(), 1)

        # Second grant (simulating Step 6/7 Webhook arrival for the same order)
        updated_order_2, newly_granted_2 = EntitlementEngine.grant_access_for_order(order)
        self.assertFalse(newly_granted_2, "Second grant should be recognized as already paid")
        self.assertEqual(Entitlement.objects.filter(user=self.user, chapter=self.chapter).count(), 1, "Must not create duplicate entitlement")

    def test_webhook_event_duplicate_payload(self):
        """Webhook receives same event twice: processed only once"""
        order = Order.objects.create(
            user=self.user,
            status='CREATED',
            net_amount_paise=4900,
            razorpay_order_id='order_wh_test_123'
        )
        OrderItem.objects.create(
            order=order,
            product=self.prod_ch,
            product_title=self.prod_ch.title,
            product_type='CHAPTER',
            original_price_paise=4900,
            final_price_paise=4900
        )

        payload = {
            "id": "evt_unique_12345",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_wh_1",
                        "order_id": "order_wh_test_123",
                        "amount": 4900
                    }
                }
            }
        }
        body_bytes = json.dumps(payload).encode('utf-8')
        sig = hmac.new(self.webhook_secret.encode('utf-8'), body_bytes, hashlib.sha256).hexdigest()

        with override_settings(RAZORPAY_WEBHOOK_SECRET=self.webhook_secret):
            # First arrival
            res1 = self.client.post('/api/payments/webhook/', body_bytes, content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE=sig)
            self.assertEqual(res1.status_code, 200)
            order.refresh_from_db()
            self.assertEqual(order.status, 'PAID')

            # Second arrival of same event
            res2 = self.client.post('/api/payments/webhook/', body_bytes, content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE=sig)
            self.assertEqual(res2.status_code, 200)
            self.assertIn("Already processed", res2.json().get('status', ''))
            self.assertEqual(WebhookEvent.objects.filter(event_id="evt_unique_12345").count(), 1)

    def test_pass_extension_before_expiry(self):
        """Rule 3: Buying again before expiry extends from the old expiry date"""
        now = timezone.now()
        # Student buys monthly pass initially
        order1 = Order.objects.create(user=self.user, status='CREATED', net_amount_paise=29900)
        OrderItem.objects.create(
            order=order1, product=self.prod_monthly_pass,
            product_title=self.prod_monthly_pass.title, product_type='MONTHLY_PASS',
            original_price_paise=29900, final_price_paise=29900
        )
        EntitlementEngine.grant_access_for_order(order1)

        ent = Entitlement.objects.get(user=self.user, entitlement_type='CLASS_PASS', class_level=self.class10)
        initial_expiry = ent.expires_at
        self.assertAlmostEqual((initial_expiry - now).days, 30, delta=1)

        # Student buys another monthly pass before expiry
        order2 = Order.objects.create(user=self.user, status='CREATED', net_amount_paise=29900)
        OrderItem.objects.create(
            order=order2, product=self.prod_monthly_pass,
            product_title=self.prod_monthly_pass.title, product_type='MONTHLY_PASS',
            original_price_paise=29900, final_price_paise=29900
        )
        EntitlementEngine.grant_access_for_order(order2)

        ent.refresh_from_db()
        # Extended expiry must be initial_expiry + 30 days = now + 60 days!
        expected_expiry = initial_expiry + timedelta(days=30)
        self.assertEqual(ent.expires_at.date(), expected_expiry.date())
        self.assertAlmostEqual((ent.expires_at - now).days, 60, delta=1)
