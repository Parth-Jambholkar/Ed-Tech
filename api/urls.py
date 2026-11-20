from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'orders', views.OrderViewSet, basename='order')

urlpatterns = [
    # Auth
    path('auth/register/', views.register_view, name='auth_register'),
    path('auth/login/', views.login_view, name='auth_login'),
    path('auth/otp-mock/', views.otp_mock_view, name='auth_otp_mock'),
    path('auth/me/', views.me_view, name='auth_me'),

    # Catalogue & Search
    path('catalogue/hierarchy/', views.catalogue_hierarchy_view, name='catalogue_hierarchy'),
    path('catalogue/search/', views.search_catalogue_view, name='catalogue_search'),

    # Cart & Pricing
    path('cart/calculate/', views.cart_calculate_view, name='cart_calculate'),

    # Checkout & Razorpay
    path('orders/create/', views.order_create_view, name='order_create'),
    path('orders/verify/', views.order_verify_view, name='order_verify'),
    path('orders/<int:order_id>/simulate-test-payment/', views.simulate_test_payment_view, name='order_simulate_payment'),
    path('payments/webhook/', views.razorpay_webhook_view, name='razorpay_webhook'),

    # Library & Secure Materials
    path('library/', views.my_library_view, name='my_library'),
    path('materials/<int:material_id>/content/', views.material_content_view, name='material_content'),

    # Admin Management
    path('admin/boards/', views.admin_boards_view, name='admin_boards'),
    path('admin/boards/<int:pk>/', views.admin_boards_view, name='admin_board_detail'),
    path('admin/classes/', views.admin_classes_view, name='admin_classes'),
    path('admin/classes/<int:pk>/', views.admin_classes_view, name='admin_class_detail'),
    path('admin/subjects/', views.admin_subjects_view, name='admin_subjects'),
    path('admin/subjects/<int:pk>/', views.admin_subjects_view, name='admin_subject_detail'),
    path('admin/chapters/', views.admin_chapters_view, name='admin_chapters'),
    path('admin/chapters/<int:pk>/', views.admin_chapters_view, name='admin_chapter_detail'),
    path('admin/products/', views.admin_products_view, name='admin_products'),
    path('admin/products/<int:pk>/', views.admin_products_view, name='admin_product_detail'),
    path('admin/coupons/', views.admin_coupons_view, name='admin_coupons'),
    path('admin/coupons/<int:pk>/', views.admin_coupons_view, name='admin_coupon_detail'),
    path('admin/orders/', views.admin_orders_view, name='admin_orders'),
    path('admin/access/manage/', views.admin_manage_access_view, name='admin_manage_access'),
    path('admin/access/logs/', views.admin_manual_logs_view, name='admin_access_logs'),
    path('admin/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),

    # Routers (products, orders)
    path('', include(router.urls)),
]
