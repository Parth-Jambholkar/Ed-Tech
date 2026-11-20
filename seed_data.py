import os
import django
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'edtech_backend.settings')
django.setup()

from django.core.files.base import ContentFile
from django.utils import timezone
from api.models import (
    User, Board, ClassLevel, Subject, Chapter, StudyMaterial,
    Product, Coupon
)

def create_sample_pdf(title, summary):
    pdf_template = f"""%PDF-1.4
1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj
2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj
3 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R>> endobj
4 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj
5 0 obj <</Length 200>> stream
BT
/F1 18 Tf
50 720 Td
({title}) Tj
/F1 11 Tf
0 -30 Td
({summary[:80]}) Tj
0 -20 Td
(Official Study Notes - EdTech Academy) Tj
0 -20 Td
(Official curriculum notes for student learning and revision.) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000325 00000 n 
trailer <</Size 6 /Root 1 0 R>>
startxref
570
%%EOF"""
    return pdf_template.encode('latin1')


def run_seed():
    print("🌱 Starting Seed Data Generation...")

    # 1. Accounts
    admin_user, created = User.objects.get_or_create(
        username='admin',
        defaults={
            'email': 'admin@edtech.com',
            'role': 'ADMIN',
            'is_staff': True,
            'is_superuser': True,
            'first_name': 'System',
            'last_name': 'Admin'
        }
    )
    if created:
        admin_user.set_password('Admin@123')
        admin_user.save()
        print("  ✓ Created Admin: admin@edtech.com / Admin@123")
    else:
        print("  ✓ Admin already exists: admin@edtech.com")

    student_user, created = User.objects.get_or_create(
        username='student',
        defaults={
            'email': 'student@edtech.com',
            'role': 'STUDENT',
            'first_name': 'Rahul',
            'last_name': 'Sharma',
            'phone_number': '9876543210'
        }
    )
    if created:
        student_user.set_password('Student@123')
        student_user.save()
        print("  ✓ Created Student: student@edtech.com / Student@123")
    else:
        print("  ✓ Student already exists: student@edtech.com")

    # 2. Board
    cbse_board, _ = Board.objects.get_or_create(
        code='CBSE',
        defaults={
            'name': 'Central Board of Secondary Education (CBSE)',
            'description': 'National level board of education in India for public and private schools.'
        }
    )
    print(f"  ✓ Board: {cbse_board.name}")

    # 3. Classes (2 Classes: Class 10 & Class 11)
    classes_data = [
        {"name": "Class 10", "grade": 10, "order": 1},
        {"name": "Class 11", "grade": 11, "order": 2},
    ]
    created_classes = []
    for cd in classes_data:
        cl, _ = ClassLevel.objects.get_or_create(
            board=cbse_board,
            name=cd["name"],
            defaults={"grade_number": cd["grade"], "order": cd["order"]}
        )
        created_classes.append(cl)
    print(f"  ✓ Classes: {[c.name for c in created_classes]}")

    # 4. Subjects (3 Subjects: Physics, Chemistry, Mathematics)
    subjects_config = [
        {"name": "Physics", "code": "PHY", "desc": "Fundamentals of mechanics, waves, optics, and electromagnetism."},
        {"name": "Chemistry", "code": "CHEM", "desc": "Chemical bonding, thermodynamics, organic, and inorganic chemistry."},
        {"name": "Mathematics", "code": "MATH", "desc": "Algebra, calculus, trigonometry, coordinate geometry, and statistics."},
    ]

    # 5 Chapters per subject with rich content
    chapters_curriculum = {
        "Physics": [
            ("Light: Reflection and Refraction", True, "Mirrors, lenses, ray diagrams, refractive index, and lens formula."), # FREE SAMPLE!
            ("Human Eye and Colorful World", False, "Structure of the eye, defects of vision, atmospheric refraction, dispersion."),
            ("Electricity and Electric Circuits", False, "Ohm's law, resistance, series and parallel circuits, electric power."),
            ("Magnetic Effects of Electric Current", False, "Magnetic fields, electromagnetic induction, Fleming's rules, AC/DC generator."),
            ("Sources of Energy and Sustainability", False, "Conventional and non-conventional energy, nuclear energy, environmental consequences.")
        ],
        "Chemistry": [
            ("Chemical Reactions and Equations", True, "Balancing equations, types of reactions, oxidation, reduction, corrosion."), # FREE SAMPLE!
            ("Acids, Bases and Salts", False, "pH scale, neutralization, indicator tests, preparation of bleaching powder, baking soda."),
            ("Metals and Non-metals", False, "Physical/chemical properties, reactivity series, metallurgy, corrosion prevention."),
            ("Carbon and its Compounds", False, "Covalent bonding, homologous series, functional groups, combustion, saponification."),
            ("Periodic Classification of Elements", False, "Mendeleev's periodic table, modern periodic law, trends in valency and atomic size.")
        ],
        "Mathematics": [
            ("Real Numbers and Number Systems", True, "Euclid's division lemma, fundamental theorem of arithmetic, irrational numbers."), # FREE SAMPLE!
            ("Polynomials and Quadratic Equations", False, "Geometrical meaning of zeroes, quadratic formulas, nature of roots."),
            ("Linear Equations in Two Variables", False, "Graphical and algebraic methods: substitution, elimination, cross-multiplication."),
            ("Triangles and Similarity Criteria", False, "Basic proportionality theorem, criteria for similarity of triangles, Pythagoras theorem."),
            ("Introduction to Trigonometry", False, "Trigonometric ratios, values for specific angles, trigonometric identities.")
        ]
    }

    all_chapters = []
    all_subjects = []

    for cl in created_classes:
        for sub_cfg in subjects_config:
            subject, _ = Subject.objects.get_or_create(
                class_level=cl,
                name=sub_cfg["name"],
                defaults={"code": f"{sub_cfg['code']}-{cl.grade_number}", "description": sub_cfg["desc"]}
            )
            all_subjects.append(subject)

            # Create Subject Pack Product (Lifetime access, ₹499 = 49900 paise)
            Product.objects.get_or_create(
                product_type='SUBJECT',
                target_subject=subject,
                defaults={
                    "title": f"{subject.name} Complete Subject Pack ({cl.name})",
                    "description": f"Full lifetime access to all current and future chapters in {subject.name} for {cl.name}.",
                    "price_paise": 49900,  # ₹499
                    "duration_days": None,  # Lifetime
                    "is_active": True
                }
            )

            # Create 5 Chapters
            ch_list = chapters_curriculum.get(sub_cfg["name"], [])
            for idx, (ch_title, is_free, ch_desc) in enumerate(ch_list, start=1):
                chapter, _ = Chapter.objects.get_or_create(
                    subject=subject,
                    chapter_number=idx,
                    defaults={
                        "title": ch_title,
                        "description": ch_desc,
                        "is_free": is_free
                    }
                )
                all_chapters.append(chapter)

                # Attach PDF Study Material to Chapter
                pdf_bytes = create_sample_pdf(f"{subject.name} - Ch {idx}: {ch_title}", ch_desc)
                mat, m_created = StudyMaterial.objects.get_or_create(
                    chapter=chapter,
                    material_type='PDF',
                    defaults={
                        "title": f"Complete Notes - {ch_title}",
                        "duration_mins": 25,
                        "content_html": f"""
<div class="study-notes-container">
  <h2>{chapter.title}</h2>
  <p class="summary"><em>{ch_desc}</em></p>
  <hr/>
  <h3>1. Core Concepts & Definitions</h3>
  <p>This chapter provides in-depth conceptual clarity designed according to latest board syllabus standards.</p>
  <div class="note-callout">
    <strong>Key Principle:</strong> Understanding the fundamentals enables solving high-order thinking (HOTS) questions with speed and precision.
  </div>
  <h3>2. Essential Formulas & Derivations</h3>
  <ul>
    <li>Fundamental Equation: <code>E = mc&sup2;</code> or <code>V = IR</code></li>
    <li>Proportionality Factor: <code>k = 1 / (4 &pi; &epsilon;)</code></li>
    <li>Standard Constant Values & Units</li>
  </ul>
  <h3>3. Solved Practice Questions</h3>
  <p><strong>Example 1:</strong> Calculate the resulting quantity given standard laboratory parameters. <br/>
  <em>Solution:</em> Substitute given values into the governing relationship and verify dimensional consistency.</p>
  <h3>4. Chapter Summary & Revision Checklist</h3>
  <p>Make sure you can define all primary terms, state the conservation laws, and sketch the necessary ray or circuit diagrams from memory.</p>
</div>
"""
                    }
                )
                if m_created or not mat.file:
                    mat.file.save(f"notes_{chapter.id}.pdf", ContentFile(pdf_bytes), save=True)

                # Create Chapter notes product (Lifetime access, ₹49 = 4900 paise)
                # If chapter is free sample, product is either free or optional
                if not is_free:
                    Product.objects.get_or_create(
                        product_type='CHAPTER',
                        target_chapter=chapter,
                        defaults={
                            "title": f"Chapter Notes: {ch_title}",
                            "description": f"Comprehensive PDF study notes, formulas, and practice quiz for {ch_title}.",
                            "price_paise": 4900,  # ₹49
                            "duration_days": None,  # Lifetime
                            "is_active": True
                        }
                    )

        # Create Class Pass Products (Monthly Pass ₹299, Annual Pass ₹2499)
        Product.objects.get_or_create(
            product_type='MONTHLY_PASS',
            target_class=cl,
            defaults={
                "title": f"{cl.name} All-Access Monthly Pass (30 Days)",
                "description": f"Unlimited 30-day access to all subjects, chapters, and materials in {cl.name}.",
                "price_paise": 29900,  # ₹299
                "duration_days": 30,
                "is_active": True
            }
        )

        Product.objects.get_or_create(
            product_type='ANNUAL_PASS',
            target_class=cl,
            defaults={
                "title": f"{cl.name} All-Access Annual Pass (365 Days)",
                "description": f"Unlimited 365-day access to every subject and chapter for complete academic year prep.",
                "price_paise": 249900,  # ₹2,499
                "duration_days": 365,
                "is_active": True
            }
        )

    # 5. Hand-picked Bundle Product
    class10 = created_classes[0]
    bundle_prod, b_created = Product.objects.get_or_create(
        product_type='BUNDLE',
        title="Class 10 STEM Super Bundle (Physics + Chemistry)",
        defaults={
            "description": "Special combo: complete lifetime access to all Class 10 Physics and Chemistry chapters at an exclusive discounted price.",
            "price_paise": 89900,  # ₹899 (discounted from ₹998)
            "duration_days": None,
            "is_active": True
        }
    )
    if b_created:
        stem_subs = Subject.objects.filter(class_level=class10, name__in=["Physics", "Chemistry"])
        bundle_prod.bundle_subjects.set(stem_subs)

    # 6. Coupons
    Coupon.objects.get_or_create(
        code='WELCOME10',
        defaults={
            "discount_type": 'PERCENTAGE',
            "discount_value": 10,  # 10% discount
            "min_order_amount_paise": 0,
            "max_discount_paise": 50000,
            "usage_limit": 5000,
            "is_active": True
        }
    )
    Coupon.objects.get_or_create(
        code='FLAT50',
        defaults={
            "discount_type": 'FLAT',
            "discount_value": 5000,  # ₹50 discount
            "min_order_amount_paise": 20000,  # Minimum ₹200 order
            "usage_limit": 500,
            "is_active": True
        }
    )
    print("  ✓ Created Coupons: WELCOME10 (10% off), FLAT50 (₹50 off)")

    print("\n✅ Seed Data Finished Successfully!")
    print("  Total Classes:", ClassLevel.objects.count())
    print("  Total Subjects:", Subject.objects.count())
    print("  Total Chapters:", Chapter.objects.count())
    print("  Total Free Sample Chapters:", Chapter.objects.filter(is_free=True).count())
    print("  Total Products:", Product.objects.count())
    print("  Admin Credentials: admin@edtech.com / Admin@123")
    print("  Student Credentials: student@edtech.com / Student@123")

if __name__ == '__main__':
    run_seed()
