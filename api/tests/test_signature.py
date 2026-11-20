import hmac
import hashlib
from django.test import TestCase, override_settings
from api.services import RazorpayService

class SignatureVerificationTests(TestCase):
    def setUp(self):
        self.secret_key = "test_secret_for_unit_tests"
        self.webhook_secret = "test_webhook_secret_key"

    def test_payment_signature_valid(self):
        """Step 5: Verifying HMAC-SHA256 of order_id + '|' + payment_id using key secret"""
        order_id = "order_9A33XWu170gUtm"
        payment_id = "pay_29Ae35XduUtikZ"

        msg = f"{order_id}|{payment_id}".encode('utf-8')
        valid_signature = hmac.new(self.secret_key.encode('utf-8'), msg, hashlib.sha256).hexdigest()

        with override_settings(RAZORPAY_KEY_SECRET=self.secret_key):
            result = RazorpayService.verify_payment_signature(order_id, payment_id, valid_signature)
            self.assertTrue(result, "Valid signature must return True")

    def test_payment_signature_tampered(self):
        """Step 5: Tampered or invalid signature must be rejected"""
        order_id = "order_9A33XWu170gUtm"
        payment_id = "pay_29Ae35XduUtikZ"
        tampered_signature = "bad_signature_0000000000000000000000000000000000000000000000000"

        with override_settings(RAZORPAY_KEY_SECRET=self.secret_key):
            result = RazorpayService.verify_payment_signature(order_id, payment_id, tampered_signature)
            self.assertFalse(result, "Tampered signature must return False")

            # Alter payment_id with original signature
            msg = f"{order_id}|{payment_id}".encode('utf-8')
            valid_sig = hmac.new(self.secret_key.encode('utf-8'), msg, hashlib.sha256).hexdigest()
            altered_result = RazorpayService.verify_payment_signature(order_id, "pay_different_hacker", valid_sig)
            self.assertFalse(altered_result, "Signature for mismatched payment_id must return False")

    def test_webhook_signature_valid_and_tampered(self):
        """Step 6: Webhook signature verification against raw body"""
        raw_body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_123"}}}}'
        valid_header = hmac.new(self.webhook_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()

        with override_settings(RAZORPAY_WEBHOOK_SECRET=self.webhook_secret):
            # Valid signature
            self.assertTrue(RazorpayService.verify_webhook_signature(raw_body, valid_header))

            # Tampered body
            tampered_body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_999"}}}}'
            self.assertFalse(RazorpayService.verify_webhook_signature(tampered_body, valid_header))

            # Tampered header
            self.assertFalse(RazorpayService.verify_webhook_signature(raw_body, "tampered_sig_12345"))
