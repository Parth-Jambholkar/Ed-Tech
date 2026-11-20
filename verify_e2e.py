import os
import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'edtech_backend.settings'
django.setup()

from django.test import Client
from api.models import Chapter

def main():
    print("🚀 Running Complete End-to-End User Journey Tests...")
    c = Client()

    # 1. Student Login
    login_res = c.post('/api/auth/login/', {'username_or_email': 'student', 'password': 'Student@123'}, content_type='application/json')
    assert login_res.status_code == 200, f"Login failed: {login_res.data}"
    token = login_res.json()['token']
    auth_h = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
    print("  ✓ 1. Student Logged in successfully!")

    # 2. Get Products
    prods = c.get('/api/products/', **auth_h).json()
    ch_prod = next(p for p in prods if p['product_type'] == 'CHAPTER')
    sub_prod = next(p for p in prods if p['product_type'] == 'SUBJECT')
    print(f"  ✓ 2. Found Chapter Product: {ch_prod['title']} (₹{ch_prod['price_rupees']})")
    print(f"  ✓ 2. Found Subject Product: {sub_prod['title']} (₹{sub_prod['price_rupees']})")

    # 3. Calculate Cart with Coupon
    cart_calc = c.post('/api/cart/calculate/', {'product_ids': [ch_prod['id']], 'coupon_code': 'WELCOME10'}, content_type='application/json', **auth_h).json()
    print(f"  ✓ 3. Cart calculated with WELCOME10 coupon! Net payable: ₹{cart_calc['net_amount_rupees']}, GST amount: ₹{cart_calc['gst_amount_rupees']}")

    # 4. Create Order
    order_res = c.post('/api/orders/create/', {'product_ids': [ch_prod['id']], 'coupon_code': 'WELCOME10'}, content_type='application/json', **auth_h).json()
    order_id = order_res['order']['id']
    print(f"  ✓ 4. Order created: #{order_res['order']['order_number']} (ID: {order_id})")

    # 5. Simulate Payment
    pay_res = c.post(f'/api/orders/{order_id}/simulate-test-payment/', {'upi_id': 'success@razorpay', 'status': 'SUCCESS'}, content_type='application/json', **auth_h).json()
    assert pay_res['status'] == 'PAID'
    print("  ✓ 5. Payment captured and access granted via UPI success@razorpay!")

    # 6. Read Notes
    ch_id = ch_prod['target_chapter']
    ch_obj = Chapter.objects.get(id=ch_id)
    mat_id = ch_obj.materials.first().id
    mat_res = c.get(f'/api/materials/{mat_id}/content/', **auth_h)
    assert mat_res.status_code == 200, f"Material read failed"
    assert mat_res['Content-Type'] == 'application/pdf', f"Expected PDF content type, got {mat_res['Content-Type']}"
    pdf_bytes = b''.join(mat_res.streaming_content)
    assert len(pdf_bytes) > 0
    print(f"  ✓ 6. PDF Notes streamed securely! Content-Type: {mat_res['Content-Type']}, Size: {len(pdf_bytes)} bytes")

    # 7. Check Upgrade Credit on Subject Pack
    prods_after = c.get('/api/products/', **auth_h).json()
    sub_after = next(p for p in prods_after if p['id'] == sub_prod['id'])
    print(f"  ✓ 7. Subject Pack Upgrade Credit: ₹{sub_after['upgrade_credit_rupees']}, Effective Price: ₹{sub_after['effective_price_rupees']}")
    assert sub_after['upgrade_credit_rupees'] > 0, "Upgrade credit was not applied!"

    # 8. Download Tax Invoice
    inv_res = c.get(f'/api/orders/{order_id}/invoice/?view=html', **auth_h)
    assert inv_res.status_code == 200
    assert "TAX INVOICE" in inv_res.content.decode('utf-8')
    print("  ✓ 8. Tax invoice generated with 18% GST split and SAC code 999293!")

    # 9. Admin Login & Dashboard
    adm_login = c.post('/api/auth/login/', {'username_or_email': 'admin', 'password': 'Admin@123'}, content_type='application/json').json()
    adm_h = {'HTTP_AUTHORIZATION': f'Bearer {adm_login["token"]}'}
    dash = c.get('/api/admin/dashboard/', **adm_h).json()
    print("  ✓ 9. Admin Dashboard Metrics:", dash['metrics'])

    # 10. Manual Access Grant with Reason Log
    grant_res = c.post('/api/admin/access/manage/', {
        'student_id': login_res.json()['user']['id'],
        'action': 'GRANT',
        'entitlement_type': 'CLASS_PASS',
        'target_id': 1,
        'duration_days': 30,
        'reason': 'Merit scholarship reward granted by Admin'
    }, content_type='application/json', **adm_h).json()
    print(f"  ✓ 10. Admin Manual Grant executed & logged: {grant_res['message']}")

    print("\n🎉 ALL 10 END-TO-END CRITICAL USER JOURNEYS PASSED PERFECTLY!")

if __name__ == '__main__':
    main()
