from django.contrib import admin
from .models import (
    User, Board, ClassLevel, Subject, Chapter, StudyMaterial,
    Product, Coupon, Order, OrderItem, Payment, WebhookEvent,
    Entitlement, ManualAccessLog
)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'role', 'phone_number', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'phone_number')

@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', 'created_at')

@admin.register(ClassLevel)
class ClassLevelAdmin(admin.ModelAdmin):
    list_display = ('name', 'board', 'grade_number', 'order', 'is_active')
    list_filter = ('board', 'is_active')

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'class_level', 'code', 'is_active')
    list_filter = ('class_level__board', 'class_level')
    search_fields = ('name',)

@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ('title', 'subject', 'chapter_number', 'is_free', 'is_active')
    list_filter = ('subject__class_level', 'is_free', 'is_active')
    search_fields = ('title',)

@admin.register(StudyMaterial)
class StudyMaterialAdmin(admin.ModelAdmin):
    list_display = ('title', 'chapter', 'material_type', 'duration_mins')
    list_filter = ('material_type',)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('title', 'product_type', 'price_paise', 'duration_days', 'is_active')
    list_filter = ('product_type', 'is_active')
    search_fields = ('title',)

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_type', 'discount_value', 'usage_limit', 'times_used', 'is_active')
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code',)

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'status', 'net_amount_paise', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('order_number', 'user__username', 'user__email')
    inlines = [OrderItemInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('razorpay_payment_id', 'order', 'user', 'amount_paise', 'status', 'created_at')
    list_filter = ('status', 'payment_method')

@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'event_type', 'processed', 'received_at')
    list_filter = ('processed', 'event_type')

@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ('user', 'entitlement_type', 'chapter', 'subject', 'class_level', 'is_active', 'expires_at')
    list_filter = ('entitlement_type', 'is_active', 'granted_by_admin')
    search_fields = ('user__username', 'user__email')

@admin.register(ManualAccessLog)
class ManualAccessLogAdmin(admin.ModelAdmin):
    list_display = ('admin_user', 'student', 'action', 'target_summary', 'created_at')
    list_filter = ('action',)
