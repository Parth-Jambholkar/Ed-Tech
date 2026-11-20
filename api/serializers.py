from rest_framework import serializers
from django.contrib.auth import authenticate
from django.utils import timezone
from django.db.models import Q
from .models import (
    User, Board, ClassLevel, Subject, Chapter, StudyMaterial,
    Product, Coupon, Order, OrderItem, Payment, Entitlement, ManualAccessLog
)
from .services import PricingEngine


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'role', 'phone_number', 'first_name', 'last_name', 'date_joined']
        read_only_fields = ['id', 'date_joined']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'role', 'phone_number', 'first_name', 'last_name']

    def create(self, validated_data):
        password = validated_data.pop('password')
        role = validated_data.get('role', 'STUDENT')
        user = User.objects.create_user(password=password, **validated_data)
        return user


class LoginSerializer(serializers.Serializer):
    username_or_email = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        login_val = attrs.get('username_or_email')
        password = attrs.get('password')

        user = None
        # Try matching email or username
        if '@' in login_val:
            try:
                user_obj = User.objects.get(email__iexact=login_val)
                user = authenticate(username=user_obj.username, password=password)
            except User.DoesNotExist:
                user = None
        else:
            user = authenticate(username=login_val, password=password)

        if not user:
            raise serializers.ValidationError("Invalid credentials provided.")
        if not user.is_active:
            raise serializers.ValidationError("Account is inactive or disabled.")

        attrs['user'] = user
        return attrs


class StudyMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyMaterial
        fields = ['id', 'chapter', 'title', 'material_type', 'duration_mins', 'order', 'created_at']


class ChapterSerializer(serializers.ModelSerializer):
    materials_count = serializers.IntegerField(source='materials.count', read_only=True)
    is_owned = serializers.SerializerMethodField()

    class Meta:
        model = Chapter
        fields = ['id', 'subject', 'title', 'chapter_number', 'description', 'is_free', 'is_active', 'materials_count', 'is_owned']

    def get_is_owned(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return obj.is_free
        if request.user.is_admin_user() or obj.is_free:
            return True
        # Check active entitlements
        user = request.user
        now = timezone.now()
        # Direct chapter
        has_ch = Entitlement.objects.filter(user=user, is_active=True, entitlement_type='CHAPTER', chapter=obj).exists()
        if has_ch:
            return True
        # Parent subject
        has_sub = Entitlement.objects.filter(user=user, is_active=True, entitlement_type='SUBJECT', subject_id=obj.subject_id).exists()
        if has_sub:
            return True
        # Parent class pass
        has_pass = Entitlement.objects.filter(
            user=user, is_active=True, entitlement_type='CLASS_PASS', class_level_id=obj.subject.class_level_id
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()
        return has_pass


class SubjectSerializer(serializers.ModelSerializer):
    chapters = ChapterSerializer(many=True, read_only=True)
    chapters_count = serializers.IntegerField(source='chapters.count', read_only=True)
    is_owned = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = ['id', 'class_level', 'name', 'code', 'description', 'is_active', 'chapters_count', 'chapters', 'is_owned']

    def get_is_owned(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        if request.user.is_admin_user():
            return True
        user = request.user
        has_sub = Entitlement.objects.filter(user=user, is_active=True, entitlement_type='SUBJECT', subject=obj).exists()
        if has_sub:
            return True
        now = timezone.now()
        has_pass = Entitlement.objects.filter(
            user=user, is_active=True, entitlement_type='CLASS_PASS', class_level_id=obj.class_level_id
        ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()
        return has_pass


class ClassLevelSerializer(serializers.ModelSerializer):
    subjects = SubjectSerializer(many=True, read_only=True)

    class Meta:
        model = ClassLevel
        fields = ['id', 'board', 'name', 'grade_number', 'order', 'is_active', 'subjects']


class BoardSerializer(serializers.ModelSerializer):
    classes = ClassLevelSerializer(many=True, read_only=True)

    class Meta:
        model = Board
        fields = ['id', 'name', 'code', 'description', 'is_active', 'created_at', 'classes']


class ProductSerializer(serializers.ModelSerializer):
    price_rupees = serializers.FloatField(source='price_in_rupees', read_only=True)
    upgrade_credit_paise = serializers.SerializerMethodField()
    upgrade_credit_rupees = serializers.SerializerMethodField()
    effective_price_paise = serializers.SerializerMethodField()
    effective_price_rupees = serializers.SerializerMethodField()
    is_owned = serializers.SerializerMethodField()
    target_info = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'product_type', 'title', 'description', 'price_paise', 'price_rupees',
            'duration_days', 'is_active', 'target_chapter', 'target_subject', 'target_class',
            'upgrade_credit_paise', 'upgrade_credit_rupees', 'effective_price_paise',
            'effective_price_rupees', 'is_owned', 'target_info', 'created_at'
        ]

    def get_target_info(self, obj):
        if obj.target_chapter:
            return {
                "type": "chapter",
                "chapter_id": obj.target_chapter.id,
                "title": obj.target_chapter.title,
                "subject": obj.target_chapter.subject.name,
                "class_level": obj.target_chapter.subject.class_level.name
            }
        elif obj.target_subject:
            return {
                "type": "subject",
                "subject_id": obj.target_subject.id,
                "name": obj.target_subject.name,
                "class_level": obj.target_subject.class_level.name,
                "chapter_count": obj.target_subject.chapters.count()
            }
        elif obj.target_class:
            return {
                "type": "class",
                "class_id": obj.target_class.id,
                "name": obj.target_class.name,
                "board": obj.target_class.board.name
            }
        return {"type": "bundle"}

    def get_upgrade_credit_paise(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0
        return PricingEngine.calculate_upgrade_credit(request.user, obj)

    def get_upgrade_credit_rupees(self, obj):
        credit = self.get_upgrade_credit_paise(obj)
        return credit / 100.0

    def get_effective_price_paise(self, obj):
        credit = self.get_upgrade_credit_paise(obj)
        return max(0, obj.price_paise - credit)

    def get_effective_price_rupees(self, obj):
        return self.get_effective_price_paise(obj) / 100.0

    def get_is_owned(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        user = request.user
        now = timezone.now()

        if obj.product_type == 'CHAPTER' and obj.target_chapter:
            if obj.target_chapter.is_free:
                return True
            has_ch = Entitlement.objects.filter(user=user, is_active=True, chapter=obj.target_chapter).exists()
            has_sub = Entitlement.objects.filter(user=user, is_active=True, entitlement_type='SUBJECT', subject=obj.target_chapter.subject).exists()
            has_pass = Entitlement.objects.filter(
                user=user, is_active=True, entitlement_type='CLASS_PASS', class_level=obj.target_chapter.subject.class_level
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()
            return has_ch or has_sub or has_pass

        elif obj.product_type == 'SUBJECT' and obj.target_subject:
            has_sub = Entitlement.objects.filter(user=user, is_active=True, entitlement_type='SUBJECT', subject=obj.target_subject).exists()
            has_pass = Entitlement.objects.filter(
                user=user, is_active=True, entitlement_type='CLASS_PASS', class_level=obj.target_subject.class_level
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()
            return has_sub or has_pass

        elif obj.product_type in ['MONTHLY_PASS', 'ANNUAL_PASS'] and obj.target_class:
            return Entitlement.objects.filter(
                user=user, is_active=True, entitlement_type='CLASS_PASS', class_level=obj.target_class
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()

        return False


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'discount_type', 'discount_value', 'min_order_amount_paise',
            'max_discount_paise', 'valid_from', 'valid_until', 'usage_limit', 'times_used', 'is_active'
        ]


class CartCalculateSerializer(serializers.Serializer):
    product_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    coupon_code = serializers.CharField(required=False, allow_blank=True, default=None)


class OrderCreateSerializer(serializers.Serializer):
    product_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    coupon_code = serializers.CharField(required=False, allow_blank=True, default=None)


class OrderVerifySerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()
    payment_method = serializers.CharField(default='UPI')


class OrderItemSerializer(serializers.ModelSerializer):
    original_price_rupees = serializers.SerializerMethodField()
    upgrade_credit_rupees = serializers.SerializerMethodField()
    final_price_rupees = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'product_title', 'product_type',
            'original_price_paise', 'original_price_rupees',
            'upgrade_credit_paise', 'upgrade_credit_rupees',
            'final_price_paise', 'final_price_rupees'
        ]

    def get_original_price_rupees(self, obj):
        return obj.original_price_paise / 100.0

    def get_upgrade_credit_rupees(self, obj):
        return obj.upgrade_credit_paise / 100.0

    def get_final_price_rupees(self, obj):
        return obj.final_price_paise / 100.0


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    coupon_code = serializers.CharField(source='coupon.code', read_only=True, default=None)
    gross_amount_rupees = serializers.SerializerMethodField()
    upgrade_credit_rupees = serializers.SerializerMethodField()
    coupon_discount_rupees = serializers.SerializerMethodField()
    net_amount_rupees = serializers.SerializerMethodField()
    gst_amount_rupees = serializers.SerializerMethodField()
    base_amount_rupees = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'user', 'user_name', 'user_email', 'status',
            'gross_amount_paise', 'gross_amount_rupees',
            'upgrade_credit_paise', 'upgrade_credit_rupees',
            'coupon', 'coupon_code', 'coupon_discount_paise', 'coupon_discount_rupees',
            'net_amount_paise', 'net_amount_rupees',
            'gst_amount_paise', 'gst_amount_rupees',
            'base_amount_paise', 'base_amount_rupees',
            'razorpay_order_id', 'created_at', 'updated_at', 'items'
        ]

    def get_gross_amount_rupees(self, obj):
        return obj.gross_amount_paise / 100.0

    def get_upgrade_credit_rupees(self, obj):
        return obj.upgrade_credit_paise / 100.0

    def get_coupon_discount_rupees(self, obj):
        return obj.coupon_discount_paise / 100.0

    def get_net_amount_rupees(self, obj):
        return obj.net_amount_paise / 100.0

    def get_gst_amount_rupees(self, obj):
        return obj.gst_amount_paise / 100.0

    def get_base_amount_rupees(self, obj):
        return obj.base_amount_paise / 100.0


class EntitlementSerializer(serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    details = serializers.SerializerMethodField()
    time_remaining_days = serializers.SerializerMethodField()

    class Meta:
        model = Entitlement
        fields = [
            'id', 'entitlement_type', 'chapter', 'subject', 'class_level',
            'title', 'details', 'granted_by_admin', 'is_active', 'valid_from',
            'expires_at', 'time_remaining_days', 'created_at'
        ]

    def get_title(self, obj):
        if obj.entitlement_type == 'CHAPTER' and obj.chapter:
            return f"Chapter: {obj.chapter.title}"
        elif obj.entitlement_type == 'SUBJECT' and obj.subject:
            return f"Subject Pack: {obj.subject.name}"
        elif obj.entitlement_type == 'CLASS_PASS' and obj.class_level:
            return f"Class Pass: {obj.class_level.name}"
        return "Entitlement"

    def get_details(self, obj):
        if obj.chapter:
            return {
                "chapter_id": obj.chapter.id,
                "title": obj.chapter.title,
                "chapter_number": obj.chapter.chapter_number,
                "subject": obj.chapter.subject.name,
                "class_level": obj.chapter.subject.class_level.name
            }
        elif obj.subject:
            return {
                "subject_id": obj.subject.id,
                "name": obj.subject.name,
                "class_level": obj.subject.class_level.name,
                "chapters_count": obj.subject.chapters.count()
            }
        elif obj.class_level:
            return {
                "class_id": obj.class_level.id,
                "name": obj.class_level.name,
                "board": obj.class_level.board.name
            }
        return {}

    def get_time_remaining_days(self, obj):
        if not obj.expires_at:
            return "Lifetime Access"
        diff = obj.expires_at - timezone.now()
        if diff.total_seconds() <= 0:
            return "Expired"
        return f"{diff.days} days left"


class ManualAccessGrantSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    action = serializers.ChoiceField(choices=['GRANT', 'REVOKE'])
    entitlement_type = serializers.ChoiceField(choices=['CHAPTER', 'SUBJECT', 'CLASS_PASS'])
    target_id = serializers.IntegerField()
    duration_days = serializers.IntegerField(required=False, default=None)
    reason = serializers.CharField(min_length=3)
