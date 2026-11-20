import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


class User(AbstractUser):
    ROLE_CHOICES = (
        ('STUDENT', 'Student'),
        ('ADMIN', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STUDENT')
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    def is_admin_user(self):
        return self.role == 'ADMIN' or self.is_superuser or self.is_staff

    def __str__(self):
        return f"{self.username} ({self.role})"


class Board(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ClassLevel(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name='classes')
    name = models.CharField(max_length=50)  # e.g. "Class 10", "Class 11"
    grade_number = models.IntegerField(default=10)
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'grade_number']
        unique_together = ('board', 'name')

    def __str__(self):
        return f"{self.board.name} - {self.name}"


class Subject(models.Model):
    class_level = models.ForeignKey(ClassLevel, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=100)  # e.g. "Physics", "Chemistry", "Mathematics"
    code = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('class_level', 'name')

    def __str__(self):
        return f"{self.class_level.name} - {self.name}"


class Chapter(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='chapters')
    title = models.CharField(max_length=200)
    chapter_number = models.IntegerField(default=1)
    description = models.TextField(blank=True)
    is_free = models.BooleanField(default=False)  # Free sample chapter per subject!
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['chapter_number']
        unique_together = ('subject', 'chapter_number')

    def __str__(self):
        return f"{self.subject.name} - Ch {self.chapter_number}: {self.title}"


class StudyMaterial(models.Model):
    MATERIAL_TYPE_CHOICES = (
        ('PDF', 'PDF Notes'),
        ('VIDEO', 'Video Lecture'),
        ('QUIZ', 'Practice Quiz'),
    )
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='materials')
    title = models.CharField(max_length=200)
    material_type = models.CharField(max_length=20, choices=MATERIAL_TYPE_CHOICES, default='PDF')
    file = models.FileField(upload_to='study_materials/', blank=True, null=True)
    content_html = models.TextField(blank=True)
    duration_mins = models.IntegerField(default=15)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.chapter.title} - {self.title} ({self.material_type})"


class Product(models.Model):
    PRODUCT_TYPE_CHOICES = (
        ('CHAPTER', 'Chapter notes'),
        ('SUBJECT', 'Subject pack'),
        ('MONTHLY_PASS', 'Monthly pass'),
        ('ANNUAL_PASS', 'Annual pass'),
        ('BUNDLE', 'Bundle'),
    )
    product_type = models.CharField(max_length=30, choices=PRODUCT_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Stored as integer in paise, inclusive of 18% GST (e.g. 4900 paise = 49 INR)
    price_paise = models.PositiveIntegerField(default=0)
    duration_days = models.IntegerField(null=True, blank=True)  # 30, 365, or null for Lifetime
    is_active = models.BooleanField(default=True)

    # Scopes
    target_chapter = models.ForeignKey(Chapter, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    target_subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    target_class = models.ForeignKey(ClassLevel, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    bundle_chapters = models.ManyToManyField(Chapter, blank=True, related_name='bundle_products')
    bundle_subjects = models.ManyToManyField(Subject, blank=True, related_name='bundle_products')

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def price_in_rupees(self):
        return self.price_paise / 100.0

    def __str__(self):
        return f"{self.title} ({self.get_product_type_display()}) - ₹{self.price_paise / 100:.2f}"


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = (
        ('PERCENTAGE', 'Percentage'),
        ('FLAT', 'Flat Amount'),
    )
    code = models.CharField(max_length=50, unique=True)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    # percentage (e.g. 10 = 10%) or flat amount in paise (e.g. 5000 = ₹50)
    discount_value = models.PositiveIntegerField()
    min_order_amount_paise = models.PositiveIntegerField(default=0)
    max_discount_paise = models.PositiveIntegerField(null=True, blank=True)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(default=1000)
    times_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def is_valid_now(self):
        now = timezone.now()
        if not self.is_active:
            return False, "Coupon is disabled."
        if self.valid_from and now < self.valid_from:
            return False, "Coupon is not yet active."
        if self.valid_until and now > self.valid_until:
            return False, "Coupon has expired."
        if self.times_used >= self.usage_limit:
            return False, "Coupon usage limit reached."
        return True, "Valid"

    def calculate_discount(self, order_amount_paise):
        if order_amount_paise < self.min_order_amount_paise:
            return 0
        if self.discount_type == 'PERCENTAGE':
            raw_discount = (order_amount_paise * self.discount_value) // 100
        else:
            raw_discount = self.discount_value

        if self.max_discount_paise:
            raw_discount = min(raw_discount, self.max_discount_paise)

        return min(raw_discount, order_amount_paise)

    def __str__(self):
        return f"{self.code} ({self.discount_type}: {self.discount_value})"


class Order(models.Model):
    STATUS_CHOICES = (
        ('CREATED', 'Created'),
        ('PENDING_UPI', 'Pending UPI'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    )
    order_number = models.CharField(max_length=64, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='CREATED')

    # All amounts stored strictly as integers in paise
    gross_amount_paise = models.PositiveIntegerField(default=0)
    upgrade_credit_paise = models.PositiveIntegerField(default=0)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    coupon_discount_paise = models.PositiveIntegerField(default=0)

    # Net amount payable by student inclusive of 18% GST
    net_amount_paise = models.PositiveIntegerField(default=0)
    # GST split: 18% inclusive
    gst_amount_paise = models.PositiveIntegerField(default=0)
    base_amount_paise = models.PositiveIntegerField(default=0)

    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = f"ORD-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_number} - {self.user.username} - ₹{self.net_amount_paise/100:.2f} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='order_items')
    product_title = models.CharField(max_length=200)
    product_type = models.CharField(max_length=30)
    original_price_paise = models.PositiveIntegerField()
    upgrade_credit_paise = models.PositiveIntegerField(default=0)
    final_price_paise = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.product_title} in {self.order.order_number}"


class Payment(models.Model):
    STATUS_CHOICES = (
        ('INITIATED', 'Initiated'),
        ('CAPTURED', 'Captured'),
        ('FAILED', 'Failed'),
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    razorpay_order_id = models.CharField(max_length=100, db_index=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    razorpay_signature = models.CharField(max_length=256, blank=True, null=True)
    payment_method = models.CharField(max_length=50, default='UPI')  # UPI, CARD, NETBANKING
    amount_paise = models.PositiveIntegerField()
    currency = models.CharField(max_length=10, default='INR')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='INITIATED')
    error_code = models.CharField(max_length=100, blank=True, null=True)
    error_description = models.TextField(blank=True, null=True)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment {self.razorpay_payment_id or 'Pending'} - {self.status} (₹{self.amount_paise/100:.2f})"


class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    signature = models.CharField(max_length=256, blank=True)
    processed = models.BooleanField(default=False)
    process_result = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Webhook {self.event_type} - {self.event_id} ({'Processed' if self.processed else 'Pending'})"


class Entitlement(models.Model):
    """
    Records who can access what and until when.
    Access checks read Entitlements directly; they do NOT re-derive access from old orders on every request.
    """
    TYPE_CHOICES = (
        ('CHAPTER', 'Chapter'),
        ('SUBJECT', 'Subject Pack'),
        ('CLASS_PASS', 'Class Pass'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='entitlements')
    entitlement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    chapter = models.ForeignKey(Chapter, null=True, blank=True, on_delete=models.CASCADE, related_name='entitlements')
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.CASCADE, related_name='entitlements')
    class_level = models.ForeignKey(ClassLevel, null=True, blank=True, on_delete=models.CASCADE, related_name='entitlements')

    source_order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name='granted_entitlements')
    granted_by_admin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    valid_from = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)  # None = Lifetime access

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_currently_valid(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.valid_from and now < self.valid_from:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True

    def __str__(self):
        target = self.chapter or self.subject or self.class_level
        expiry = self.expires_at.strftime('%Y-%m-%d') if self.expires_at else 'Lifetime'
        return f"{self.user.username} -> {self.entitlement_type}: {target} ({expiry})"


class ManualAccessLog(models.Model):
    """Audit log for admin manually granting or revoking student access"""
    admin_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='manual_actions')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='manual_access_logs')
    action = models.CharField(max_length=20, choices=[('GRANT', 'Grant Access'), ('REVOKE', 'Revoke Access')])
    entitlement = models.ForeignKey(Entitlement, null=True, blank=True, on_delete=models.SET_NULL)
    target_summary = models.CharField(max_length=255, blank=True)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} by {self.admin_user} for {self.student}: {self.reason}"
