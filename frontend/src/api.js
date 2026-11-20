/**
 * Centralized API client for Ed-Tech platform
 */
const API_BASE = '/api';

export const apiFetch = async (endpoint, options = {}) => {
  const token = localStorage.getItem('edtech_token');
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...(options.headers || {})
  };
  const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.detail || JSON.stringify(data) || 'Request failed');
  }
  return data;
};

export const authService = {
  login: (username_or_email, password) =>
    apiFetch('/auth/login/', { method: 'POST', body: JSON.stringify({ username_or_email, password }) }),
  register: (payload) =>
    apiFetch('/auth/register/', { method: 'POST', body: JSON.stringify(payload) }),
  verifyOtp: (phone_number, otp_code) =>
    apiFetch('/auth/otp-mock/', { method: 'POST', body: JSON.stringify({ phone_number, otp_code }) }),
  getProfile: () => apiFetch('/auth/me/'),
};

export const catalogueService = {
  getHierarchy: () => apiFetch('/catalogue/hierarchy/'),
  search: (q) => apiFetch(`/catalogue/search/?q=${encodeURIComponent(q)}`),
  getProducts: (params = '') => apiFetch(`/products/${params}`),
};

export const cartService = {
  calculate: (product_ids, coupon_code = '') =>
    apiFetch('/cart/calculate/', { method: 'POST', body: JSON.stringify({ product_ids, coupon_code }) }),
  createOrder: (product_ids, coupon_code = '') =>
    apiFetch('/orders/create/', { method: 'POST', body: JSON.stringify({ product_ids, coupon_code }) }),
  verifyPayment: (payload) =>
    apiFetch('/orders/verify/', { method: 'POST', body: JSON.stringify(payload) }),
  simulateTestPayment: (orderId, upiId, status) =>
    apiFetch(`/orders/${orderId}/simulate-test-payment/`, { method: 'POST', body: JSON.stringify({ upi_id: upiId, status }) }),
};

export const libraryService = {
  getMyLibrary: () => apiFetch('/library/'),
  getMaterialContent: (materialId) => apiFetch(`/materials/${materialId}/content/`),
};

export const orderService = {
  getOrders: () => apiFetch('/orders/'),
  getInvoice: (orderId) => apiFetch(`/orders/${orderId}/invoice/`),
};
