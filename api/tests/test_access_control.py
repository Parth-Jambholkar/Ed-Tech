from django.test import TestCase, Client
from api.models import User, Board, ClassLevel, Subject, Chapter, StudyMaterial, Entitlement
from api.authentication import generate_jwt_for_user

class AccessControlAndMaterialsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.board = Board.objects.create(name='CBSE', code='CBSE')
        self.class10 = ClassLevel.objects.create(board=self.board, name='Class 10', grade_number=10)
        self.physics = Subject.objects.create(class_level=self.class10, name='Physics', code='PHY10')

        self.ch_free = Chapter.objects.create(subject=self.physics, title='Free Chapter 1', chapter_number=1, is_free=True)
        self.ch_locked = Chapter.objects.create(subject=self.physics, title='Locked Chapter 2', chapter_number=2, is_free=False)

        self.mat_free = StudyMaterial.objects.create(
            chapter=self.ch_free,
            title='Free Notes',
            material_type='PDF',
            content_html='<p>Free sample notes</p>'
        )
        self.mat_locked = StudyMaterial.objects.create(
            chapter=self.ch_locked,
            title='Locked Notes',
            material_type='PDF',
            content_html='<p>Exclusive paid notes</p>'
        )

        self.student = User.objects.create_user(username='student_test', email='st@test.com', password='pass', role='STUDENT')
        self.student_token = generate_jwt_for_user(self.student)
        self.auth_headers = {'HTTP_AUTHORIZATION': f'Bearer {self.student_token}'}

        self.admin = User.objects.create_user(username='admin_test', email='adm@test.com', password='pass', role='ADMIN', is_staff=True)
        self.admin_token = generate_jwt_for_user(self.admin)
        self.admin_headers = {'HTTP_AUTHORIZATION': f'Bearer {self.admin_token}'}

    def test_locked_content_returns_403_for_unentitled_student(self):
        """S7: Locked content must be refused by the server with 403, not only hidden in the UI"""
        res = self.client.get(f'/api/materials/{self.mat_locked.id}/content/', **self.auth_headers)
        self.assertEqual(res.status_code, 403)
        self.assertIn("Access Denied", res.json().get('error', ''))

    def test_free_sample_chapter_accessible_by_student(self):
        """Free sample chapter accessible by any logged-in student"""
        res = self.client.get(f'/api/materials/{self.mat_free.id}/content/', **self.auth_headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Free sample", res.json().get('access_granted_reason', ''))

    def test_purchased_chapter_access_granted(self):
        """Student with active chapter entitlement gets 200 OK"""
        Entitlement.objects.create(
            user=self.student,
            entitlement_type='CHAPTER',
            chapter=self.ch_locked,
            is_active=True
        )
        res = self.client.get(f'/api/materials/{self.mat_locked.id}/content/', **self.auth_headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Exclusive paid notes", res.json().get('content_html', ''))

    def test_subject_pack_unlocks_all_chapters(self):
        """Student owning subject pack unlocks all chapters under it"""
        Entitlement.objects.create(
            user=self.student,
            entitlement_type='SUBJECT',
            subject=self.physics,
            is_active=True
        )
        res = self.client.get(f'/api/materials/{self.mat_locked.id}/content/', **self.auth_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get('id'), self.mat_locked.id)

    def test_admin_has_full_access_to_all_materials(self):
        """Admin has full access to any material"""
        res = self.client.get(f'/api/materials/{self.mat_locked.id}/content/', **self.admin_headers)
        self.assertEqual(res.status_code, 200)

    def test_role_based_access_control_for_admin_routes(self):
        """Every API route checks caller role. Student calling admin route gets 403"""
        res = self.client.get('/api/admin/dashboard/', **self.auth_headers)
        self.assertEqual(res.status_code, 403)

        # Admin gets 200
        res_admin = self.client.get('/api/admin/dashboard/', **self.admin_headers)
        self.assertEqual(res_admin.status_code, 200)
