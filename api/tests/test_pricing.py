from django.test import TestCase
from api.models import User, Board, ClassLevel, Subject, Chapter, Product, Coupon, Order, OrderItem
from api.services import PricingEngine, EntitlementEngine

class PricingEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_student', email='student@test.com', password='pass')
        self.board = Board.objects.create(name='Test Board', code='TB')
        self.class10 = ClassLevel.objects.create(board=self.board, name='Class 10', grade_number=10)
        self.physics = Subject.objects.create(class_level=self.class10, name='Physics', code='PHY10')

        self.ch1 = Chapter.objects.create(subject=self.physics, title='Ch 1: Motion', chapter_number=1, is_free=True)
        self.ch2 = Chapter.objects.create(subject=self.physics, title='Ch 2: Force', chapter_number=2, is_free=False)
        self.ch3 = Chapter.objects.create(subject=self.physics, title='Ch 3: Gravitation', chapter_number=3, is_free=False)

        self.prod_ch2 = Product.objects.create(
            product_type='CHAPTER',
            title='Ch 2 Notes',
            target_chapter=self.ch2,
            price_paise=4900  # ₹49
        )
        self.prod_ch3 = Product.objects.create(
            product_type='CHAPTER',
            title='Ch 3 Notes',
            target_chapter=self.ch3,
            price_paise=4900  # ₹49
        )
        self.prod_subject = Product.objects.create(
            product_type='SUBJECT',
            title='Physics Subject Pack',
            target_subject=self.physics,
            price_paise=49900  # ₹499
        )
        self.coupon_pct = Coupon.objects.create(
            code='SAVE10',
            discount_type='PERCENTAGE',
            discount_value=10,
            usage_limit=100
        )
        self.coupon_flat = Coupon.objects.create(
            code='FLAT50',
            discount_type='FLAT',
            discount_value=5000,  # ₹50
            min_order_amount_paise=10000,
            usage_limit=100
        )

    def test_gst_inclusive_split_precision(self):
        """Rule 5: Test 18% inclusive GST split precision in paise"""
        # Test ₹499 (49900 paise)
        split = PricingEngine.calculate_gst_breakdown(49900)
        base = split['base_amount_paise']
        gst = split['gst_amount_paise']
        cgst = split['cgst_amount_paise']
        sgst = split['sgst_amount_paise']

        self.assertEqual(base + gst, 49900, "Base + GST must exactly match net amount in paise")
        self.assertEqual(cgst + sgst, gst, "CGST + SGST must exactly match total GST")
        # 49900 / 1.18 = 42288.13 -> round = 42288 paise
        self.assertEqual(base, 42288)
        self.assertEqual(gst, 7612)
        self.assertEqual(cgst, 3806)
        self.assertEqual(sgst, 3806)

        # Test ₹49 (4900 paise)
        split49 = PricingEngine.calculate_gst_breakdown(4900)
        self.assertEqual(split49['base_amount_paise'] + split49['gst_amount_paise'], 4900)

    def test_upgrade_credit_calculation(self):
        """Rule 2: Student owning some chapters of a subject gets subject pack price reduced"""
        # Student buys Chapter 2 for ₹49
        order = Order.objects.create(
            user=self.user,
            status='CREATED',
            net_amount_paise=4900,
            gross_amount_paise=4900
        )
        OrderItem.objects.create(
            order=order,
            product=self.prod_ch2,
            product_title=self.prod_ch2.title,
            product_type='CHAPTER',
            original_price_paise=4900,
            final_price_paise=4900
        )
        EntitlementEngine.grant_access_for_order(order)

        # Calculate upgrade credit for subject pack
        credit = PricingEngine.calculate_upgrade_credit(self.user, self.prod_subject)
        self.assertEqual(credit, 4900, "Upgrade credit should be equal to amount paid for chapter")

        # Cart calculation for subject pack should reflect this credit
        cart = PricingEngine.calculate_cart_totals(self.user, [self.prod_subject.id])
        self.assertEqual(cart['upgrade_credit_paise'], 4900)
        self.assertEqual(cart['net_amount_paise'], 49900 - 4900)  # 45000 paise (₹450)

    def test_coupon_discount_calculation(self):
        """Rule 4: Admin coupon applies correctly at checkout"""
        # 10% coupon on 49900 paise -> 4990 paise discount
        cart = PricingEngine.calculate_cart_totals(self.user, [self.prod_subject.id], coupon_code='SAVE10')
        self.assertEqual(cart['coupon_discount_paise'], 4990)
        self.assertEqual(cart['net_amount_paise'], 49900 - 4990)

        # Flat ₹50 coupon on 49900 paise -> 5000 paise discount
        cart_flat = PricingEngine.calculate_cart_totals(self.user, [self.prod_subject.id], coupon_code='FLAT50')
        self.assertEqual(cart_flat['coupon_discount_paise'], 5000)
        self.assertEqual(cart_flat['net_amount_paise'], 49900 - 5000)

    def test_overlapping_purchase_prevention(self):
        """Rule 1: Student who owns subject pack cannot buy chapter inside it"""
        # User owns subject pack
        order = Order.objects.create(user=self.user, status='CREATED', net_amount_paise=49900)
        OrderItem.objects.create(
            order=order,
            product=self.prod_subject,
            product_title=self.prod_subject.title,
            product_type='SUBJECT',
            original_price_paise=49900,
            final_price_paise=49900
        )
        EntitlementEngine.grant_access_for_order(order)

        # Attempting to buy ch2 should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            PricingEngine.calculate_cart_totals(self.user, [self.prod_ch2.id])
        self.assertIn("already own the subject pack", str(ctx.exception))
