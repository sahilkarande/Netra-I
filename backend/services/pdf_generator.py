"""
Professional PDF Report Generator for Student Exam Results
Enhanced with detailed questions, answers, and modern design
Requires: pip install reportlab
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, 
    Spacer, PageBreak, KeepTogether, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.platypus.flowables import HRFlowable
from io import BytesIO
from datetime import datetime


# Professional Color Palette
PRIMARY_COLOR = colors.HexColor('#2563eb')      # Blue
SUCCESS_COLOR = colors.HexColor('#10b981')      # Green
DANGER_COLOR = colors.HexColor('#ef4444')       # Red
WARNING_COLOR = colors.HexColor('#f59e0b')      # Orange
LIGHT_BG = colors.HexColor('#f8fafc')           # Light gray
HEADER_BG = colors.HexColor('#1e40af')          # Dark blue
TEXT_DARK = colors.HexColor('#1e293b')          # Dark text
TEXT_LIGHT = colors.HexColor('#64748b')         # Light text


class NumberedCanvas(canvas.Canvas):
    """Custom canvas with page numbers and watermark"""
    
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_elements(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_elements(self, page_count):
        """Draw page number, footer, and header elements"""
        page_width, page_height = A4
        
        # Header line
        self.setStrokeColor(PRIMARY_COLOR)
        self.setLineWidth(2)
        self.line(40, page_height - 40, page_width - 40, page_height - 40)
        
        # Footer line
        self.setLineWidth(1)
        self.line(40, 40, page_width - 40, 40)
        
        # Page number
        self.setFont("Helvetica", 9)
        self.setFillColor(TEXT_LIGHT)
        self.drawCentredString(
            page_width / 2, 25,
            f"Page {self._pageNumber} of {page_count}"
        )
        
        # Footer text
        self.setFont("Helvetica", 8)
        self.drawString(
            50, 25,
            "Proctored Exam Platform"
        )
        self.drawRightString(
            page_width - 50, 25,
            f"Generated: {datetime.now().strftime('%d %b %Y')}"
        )


def create_custom_styles():
    """Create custom paragraph styles for the document"""
    styles = getSampleStyleSheet()
    
    # Main title
    styles.add(ParagraphStyle(
        name='MainTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=HEADER_BG,
        spaceAfter=8,
        spaceBefore=10,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
        leading=32
    ))
    
    # Subtitle
    styles.add(ParagraphStyle(
        name='SubTitle',
        parent=styles['Normal'],
        fontSize=13,
        textColor=TEXT_LIGHT,
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName='Helvetica-Oblique'
    ))
    
    # Section heading
    styles.add(ParagraphStyle(
        name='SectionHeading',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=PRIMARY_COLOR,
        spaceAfter=10,
        spaceBefore=15,
        fontName='Helvetica-Bold',
        borderWidth=0,
        borderColor=PRIMARY_COLOR,
        borderPadding=5,
        leftIndent=0
    ))
    
    # Question text
    styles.add(ParagraphStyle(
        name='QuestionText',
        parent=styles['Normal'],
        fontSize=11,
        textColor=TEXT_DARK,
        spaceAfter=8,
        spaceBefore=6,
        alignment=TA_JUSTIFY,
        fontName='Helvetica-Bold',
        leading=14
    ))
    
    # Option text
    styles.add(ParagraphStyle(
        name='OptionText',
        parent=styles['Normal'],
        fontSize=10,
        textColor=TEXT_DARK,
        spaceAfter=4,
        leftIndent=20,
        fontName='Helvetica',
        leading=13
    ))
    
    # Info box
    styles.add(ParagraphStyle(
        name='InfoBox',
        parent=styles['Normal'],
        fontSize=9,
        textColor=TEXT_LIGHT,
        alignment=TA_CENTER,
        fontName='Helvetica',
        leading=11
    ))
    
    return styles


def create_header_section(student, exam, student_exam, styles):
    """Create the header section with title and exam info"""
    elements = []
    
    # Main title with icon
    title = Paragraph("📋 EXAMINATION RESULT REPORT", styles['MainTitle'])
    elements.append(title)
    
    # Institution/Platform name
    subtitle = Paragraph("Proctored Examination Platform", styles['SubTitle'])
    elements.append(subtitle)
    
    # Divider
    elements.append(HRFlowable(
        width="100%",
        thickness=1,
        color=PRIMARY_COLOR,
        spaceBefore=5,
        spaceAfter=15
    ))
    
    return elements


def create_info_cards(student, exam, student_exam, styles):
    """Create student and exam information cards"""
    elements = []
    
    # Two-column layout for student and exam info
    # Student Information Card
    elements.append(Paragraph("Student Information", styles['SectionHeading']))
    
    student_data = [
        ['Full Name:', Paragraph(f"<b>{student.full_name or student.username}</b>", styles['Normal'])],
        ['Username:', student.username],
        ['Email:', student.email],
    ]
    
    if student.role == 'student':
        student_data.extend([
            ['PRN Number:', student.prn_number or 'N/A'],
            ['Roll Number:', student.roll_id or 'N/A'],
            ['Batch:', student.batch or 'N/A'],
            ['Department:', student.department or 'N/A'],
        ])
    
    student_table = Table(student_data, colWidths=[2*inch, 4.5*inch])
    student_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), TEXT_DARK),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (0, -1), 15),
        ('RIGHTPADDING', (0, 0), (0, -1), 15),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(student_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Exam Information Card
    elements.append(Paragraph("Examination Details", styles['SectionHeading']))
    
    exam_data = [
        ['Exam Title:', Paragraph(f"<b>{exam.title}</b>", styles['Normal'])],
        ['Description:', exam.description[:80] + '...' if exam.description and len(exam.description) > 80 else exam.description or 'N/A'],
        ['Duration:', f"{exam.duration_minutes} minutes"],
        ['Total Questions:', str(len(exam.questions))],
        ['Total Marks:', f"{student_exam.total_points:.1f}"],
        ['Passing Criteria:', f"{exam.passing_score}%"],
        ['Date & Time:', student_exam.submitted_at.strftime('%d %B %Y at %I:%M %p') if student_exam.submitted_at else 'N/A'],
        ['Time Taken:', f"{student_exam.time_taken_minutes} minutes" if student_exam.time_taken_minutes else 'N/A'],
    ]
    
    exam_table = Table(exam_data, colWidths=[2*inch, 4.5*inch])
    exam_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), TEXT_DARK),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (0, -1), 15),
        ('RIGHTPADDING', (0, 0), (0, -1), 15),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(exam_table)
    elements.append(Spacer(1, 0.3*inch))
    
    return elements


def create_performance_card(student_exam, exam, answers_with_questions, styles):
    """Create the performance summary card with visual appeal"""
    elements = []
    
    elements.append(Paragraph("Performance Summary", styles['SectionHeading']))
    
    # Calculate statistics
    total_questions = len(answers_with_questions)
    correct_count = sum(1 for item in answers_with_questions if item['answer'].is_correct)
    incorrect_count = total_questions - correct_count
    accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
    
    # Determine pass/fail
    passed = student_exam.passed
    status_text = "✓ PASSED" if passed else "✗ FAILED"
    status_bg = colors.HexColor('#d1fae5') if passed else colors.HexColor('#fee2e2')
    status_text_color = colors.HexColor('#065f46') if passed else colors.HexColor('#991b1b')
    
    # Performance data
    perf_data = [
        ['Score Obtained:', f"{student_exam.score:.1f} / {student_exam.total_points:.1f}"],
        ['Percentage:', f"{student_exam.percentage:.2f}%"],
        ['Accuracy:', f"{accuracy:.1f}%"],
        ['Questions Attempted:', f"{total_questions}"],
        ['Correct Answers:', f"{correct_count}"],
        ['Wrong Answers:', f"{incorrect_count}"],
        ['Final Result:', status_text],
    ]
    
    perf_table = Table(perf_data, colWidths=[2.2*inch, 4.3*inch])
    
    table_style = [
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_BG),
        ('BACKGROUND', (0, 6), (-1, 6), status_bg),
        ('TEXTCOLOR', (0, 0), (-1, 5), TEXT_DARK),
        ('TEXTCOLOR', (0, 6), (-1, 6), status_text_color),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, 5), 'Helvetica'),
        ('FONTNAME', (1, 6), (1, 6), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 5), 11),
        ('FONTSIZE', (0, 6), (-1, 6), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (0, -1), 15),
        ('RIGHTPADDING', (0, 0), (0, -1), 15),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    
    perf_table.setStyle(TableStyle(table_style))
    elements.append(perf_table)
    elements.append(Spacer(1, 0.3*inch))
    
    return elements


def create_summary_table(answers_with_questions, styles):
    """Create a compact summary table of all questions"""
    elements = []
    
    elements.append(Paragraph("Quick Summary", styles['SectionHeading']))
    
    # Header
    summary_data = [
        [Paragraph('<b>Q#</b>', styles['Normal']), 
         Paragraph('<b>Your Answer</b>', styles['Normal']),
         Paragraph('<b>Correct Answer</b>', styles['Normal']),
         Paragraph('<b>Result</b>', styles['Normal']),
         Paragraph('<b>Points</b>', styles['Normal'])]
    ]
    
    # Question rows
    for idx, item in enumerate(answers_with_questions, 1):
        question = item['question']
        answer = item['answer']
        
        result_icon = '✓' if answer.is_correct else '✗'
        result_color = '<font color="green">✓</font>' if answer.is_correct else '<font color="red">✗</font>'
        your_answer = answer.selected_answer or 'Not Answered'
        
        summary_data.append([
            str(idx),
            your_answer,
            question.correct_answer,
            Paragraph(result_color, styles['Normal']),
            f"{answer.points_earned:.1f}/{question.points:.1f}"
        ])
    
    summary_table = Table(
        summary_data, 
        colWidths=[0.5*inch, 1.3*inch, 1.3*inch, 0.8*inch, 1*inch]
    )
    
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    
    # Alternate row colors
    for i in range(1, len(summary_data)):
        if i % 2 == 0:
            table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f8fafc')))
    
    summary_table.setStyle(TableStyle(table_style))
    elements.append(summary_table)
    elements.append(Spacer(1, 0.3*inch))
    
    return elements


def create_detailed_questions(answers_with_questions, styles):
    """Create detailed view of each question with all options"""
    elements = []
    
    elements.append(PageBreak())
    elements.append(Paragraph("Detailed Question Analysis", styles['SectionHeading']))
    elements.append(Spacer(1, 0.15*inch))
    
    for idx, item in enumerate(answers_with_questions, 1):
        question = item['question']
        answer = item['answer']
        
        # Question box elements
        question_elements = []
        
        # Question header
        is_correct = answer.is_correct
        header_bg = colors.HexColor('#d1fae5') if is_correct else colors.HexColor('#fee2e2')
        header_text_color = colors.HexColor('#065f46') if is_correct else colors.HexColor('#991b1b')
        
        header_data = [[
            Paragraph(f"<b>Question {idx}</b>", styles['Normal']),
            Paragraph(f"<b>{'✓ Correct' if is_correct else '✗ Incorrect'}</b>", styles['Normal']),
            Paragraph(f"<b>{answer.points_earned:.1f}/{question.points:.1f} points</b>", styles['Normal'])
        ]]
        
        header_table = Table(header_data, colWidths=[3.3*inch, 1.8*inch, 1.4*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), header_bg),
            ('TEXTCOLOR', (0, 0), (-1, 0), header_text_color),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (-1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('LEFTPADDING', (0, 0), (-1, 0), 12),
            ('RIGHTPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ]))
        
        question_elements.append(header_table)
        
        # Question text
        q_text = Paragraph(f"<b>Q.</b> {question.question_text}", styles['QuestionText'])
        question_elements.append(Spacer(1, 0.08*inch))
        question_elements.append(q_text)
        question_elements.append(Spacer(1, 0.1*inch))
        
        # Options
        options = []
        option_labels = ['A', 'B', 'C', 'D']
        option_values = [question.option_a, question.option_b, question.option_c, question.option_d]
        
        for label, option_text in zip(option_labels, option_values):
            if option_text:  # Only show non-empty options
                # Determine styling based on correct answer and student's answer
                is_correct_option = (label == question.correct_answer)
                is_student_answer = (label == answer.selected_answer)
                
                if is_correct_option and is_student_answer:
                    # Student selected correct answer
                    prefix = "✓"
                    style_text = f'<font color="#065f46"><b>{prefix} ({label}) {option_text}</b></font>'
                elif is_correct_option:
                    # Correct answer (not selected)
                    prefix = "✓"
                    style_text = f'<font color="#059669"><b>{prefix} ({label}) {option_text}</b></font>'
                elif is_student_answer:
                    # Student selected wrong answer
                    prefix = "✗"
                    style_text = f'<font color="#dc2626"><b>{prefix} ({label}) {option_text}</b></font>'
                else:
                    # Other options
                    style_text = f'({label}) {option_text}'
                
                option_para = Paragraph(style_text, styles['OptionText'])
                options.append(option_para)
                options.append(Spacer(1, 0.04*inch))
        
        question_elements.extend(options)
        
        # Answer explanation
        question_elements.append(Spacer(1, 0.08*inch))
        
        answer_info_data = [[
            Paragraph(f"<b>Your Answer:</b> {answer.selected_answer or 'Not Answered'}", styles['Normal']),
            Paragraph(f"<b>Correct Answer:</b> {question.correct_answer}", styles['Normal'])
        ]]
        
        answer_info_table = Table(answer_info_data, colWidths=[3.3*inch, 3.2*inch])
        answer_info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), LIGHT_BG),
            ('TEXTCOLOR', (0, 0), (-1, 0), TEXT_DARK),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('LEFTPADDING', (0, 0), (-1, 0), 10),
            ('RIGHTPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ]))
        
        question_elements.append(answer_info_table)
        
        # Keep question together on same page
        elements.append(KeepTogether(question_elements))
        elements.append(Spacer(1, 0.2*inch))
        
        # Add page break after every 3 questions for better readability
        if idx % 3 == 0 and idx < len(answers_with_questions):
            elements.append(PageBreak())
    
    return elements


def create_proctoring_section(student_exam, styles):
    """Create proctoring information section if applicable"""
    elements = []
    
    if student_exam.tab_switch_count > 0 or student_exam.suspicious_activity_count > 0:
        elements.append(PageBreak())
        elements.append(Paragraph("Proctoring Report", styles['SectionHeading']))
        elements.append(Spacer(1, 0.1*inch))
        
        # Warning message
        warning_text = Paragraph(
            "<b>⚠️ Note:</b> This exam was proctored. The following activities were detected during the examination:",
            styles['Normal']
        )
        elements.append(warning_text)
        elements.append(Spacer(1, 0.15*inch))
        
        proctoring_data = [
            ['Activity Type', 'Count', 'Status'],
            ['Tab Switches', str(student_exam.tab_switch_count), 'Flagged' if student_exam.tab_switch_count > 3 else 'Normal'],
            ['Suspicious Activities', str(student_exam.suspicious_activity_count), 'Flagged' if student_exam.suspicious_activity_count > 0 else 'Normal'],
        ]
        
        proctoring_table = Table(proctoring_data, colWidths=[3*inch, 1.5*inch, 2*inch])
        
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), WARNING_COLOR),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#fbbf24')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        
        # Color code rows based on severity
        if student_exam.tab_switch_count > 3:
            table_style.append(('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#fef3c7')))
        if student_exam.suspicious_activity_count > 0:
            table_style.append(('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#fef3c7')))
        
        proctoring_table.setStyle(TableStyle(table_style))
        elements.append(proctoring_table)
        
        elements.append(Spacer(1, 0.15*inch))
        disclaimer = Paragraph(
            "<i>Note: Proctoring data is recorded for academic integrity purposes. "
            "Please contact the exam administrator if you have questions about this report.</i>",
            styles['InfoBox']
        )
        elements.append(disclaimer)
    
    return elements


def create_footer_section(styles):
    """Create document footer with metadata"""
    elements = []
    
    elements.append(Spacer(1, 0.4*inch))
    elements.append(HRFlowable(
        width="100%",
        thickness=1,
        color=TEXT_LIGHT,
        spaceBefore=10,
        spaceAfter=10
    ))
    
    footer_text = f"""
    <para alignment='center' fontSize='9' textColor='#64748b'>
        <b>CONFIDENTIAL DOCUMENT</b><br/>
        This is a computer-generated result report and does not require a physical signature.<br/>
        Generated on {datetime.now().strftime('%d %B %Y at %I:%M %p')}<br/><br/>
        <i>© 2024-2025 Proctored Exam Platform. All rights reserved.</i><br/>
        For queries or concerns, please contact your examination administrator.
    </para>
    """
    
    elements.append(Paragraph(footer_text, styles['InfoBox']))
    
    return elements

def generate_batch_report_pdf(batch_name, exam_titles, report_data, summary_data, col_notes=None):
    from reportlab.platypus import Paragraph
    from reportlab.lib.units import inch

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=50,
        bottomMargin=45,
        title=f"Batch Report - {batch_name}",
        author="Proctored Exam Platform"
    )
    
    styles = create_custom_styles()
    elements = []

    # ---------------- HEADER ----------------
    elements.append(Paragraph("📊 BATCH PERFORMANCE REPORT", styles['MainTitle']))
    
    subtitle = f"Batch: <b>{batch_name}</b> | Exams: <b>{', '.join(exam_titles)}</b>"
    elements.append(Paragraph(subtitle, styles['SubTitle']))

    elements.append(HRFlowable(
        width="100%",
        thickness=1,
        color=PRIMARY_COLOR,
        spaceBefore=10,
        spaceAfter=20
    ))

    # ---------------- SUMMARY ----------------
    elements.append(Paragraph("Summary Overview", styles['SectionHeading']))

    summary_table_data = [
        ["Total Students", summary_data['total_students']],
        ["Appeared", summary_data['appeared']],
        ["Passed", summary_data['passed']],
        ["Failed", summary_data['failed']],
        ["Absent", summary_data['absent']],
        ["Average Marks", f"{summary_data['average_marks']:.2f}"],
        ["Pass Percentage", f"{summary_data['pass_percentage']:.2f}%"]
    ]

    summary_table = Table(summary_table_data, colWidths=[2.5*inch, 3.5*inch])

    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('PADDING', (0, 0), (-1, -1), 10),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 0.3*inch))

    # ---------------- STUDENT TABLE ----------------
    elements.append(Paragraph("📋 Student-wise Performance Table", styles['SectionHeading']))

    # ✅ Wrapped headers
    wrapped_headers = [
        Paragraph("<b>PRN</b>", styles['Normal']),
        Paragraph("<b>Full Name</b>", styles['Normal'])
    ]

    for i, title in enumerate(exam_titles):
        short_title = title if len(title) <= 25 else title[:22] + "..."
        note = col_notes[i].strip() if col_notes and i < len(col_notes) and col_notes[i].strip() else ''
        header_html = f"<b>{short_title}</b>"
        if note:
            header_html += f"<br/><i><font size='7' color='#94a3b8'>{note}</font></i>"
        wrapped_headers.append(Paragraph(header_html, styles['Normal']))

    wrapped_headers.extend([
        Paragraph("<b>Total Marks</b>", styles['Normal']),
        Paragraph("<b>Percentage</b>", styles['Normal'])
    ])

    student_table_data = [wrapped_headers]

    # rows
    for row in report_data:
        student_table_data.append(
            [row['prn_number'], row['full_name']] +
            row['exam_marks'] +
            [f"{row['total_marks']:.2f}", f"{row['percentage']:.2f}%"]
        )

    # ✅ Smart column width
    exam_count = len(exam_titles)
    exam_col_width = max(0.8, min(1.2, 10 / max(1, exam_count))) * inch

    col_widths = [1.2*inch, 1.8*inch] + [exam_col_width]*exam_count + [1*inch, 1*inch]

    student_table = Table(student_table_data, colWidths=col_widths)

    # ✅ Style
    student_table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),

        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (2, 0), (-3, 0), 'LEFT'),  # exam titles left aligned

        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),

        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),

        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),

        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),

        # vertical separator after name
        ('LINEBEFORE', (2, 0), (2, -1), 1, colors.HexColor('#94a3b8')),
    ]

    # ✅ Zebra rows
    for i in range(1, len(student_table_data)):
        bg = colors.white if i % 2 else colors.HexColor('#f1f5f9')
        student_table_style.append(('BACKGROUND', (0, i), (-1, i), bg))

    student_table.setStyle(TableStyle(student_table_style))

    # ✅ Increase header height
    student_table._argH[0] = 45

    elements.append(student_table)

    # ---------------- FOOTER ----------------
    elements.append(Spacer(1, 0.4*inch))

    elements.append(HRFlowable(
        width="100%",
        thickness=1,
        color=TEXT_LIGHT,
        spaceBefore=10,
        spaceAfter=10
    ))

    footer_text = f"""
    <para alignment='center' fontSize='9' textColor='#64748b'>
        <b>CONFIDENTIAL DOCUMENT</b><br/>
        Batch Performance Report generated automatically.<br/>
        Generated on {datetime.now().strftime('%d %B %Y at %I:%M %p')}<br/><br/>
        <i>© 2024-2025 Proctored Exam Platform. All rights reserved.</i>
    </para>
    """

    elements.append(Paragraph(footer_text, styles['InfoBox']))

    doc.build(elements, canvasmaker=NumberedCanvas)

    buffer.seek(0)
    return buffer

def generate_result_pdf(student_exam, exam, student, answers_with_questions):
    """
    Generate a highly professional PDF report for student exam results
    
    Args:
        student_exam: StudentExam object
        exam: Exam object
        student: User object (student)
        answers_with_questions: List of dicts with 'question' and 'answer' keys
    
    Returns:
        BytesIO buffer containing the PDF
    """
    
    buffer = BytesIO()
    
    # Document setup with margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=50,
        leftMargin=50,
        topMargin=50,
        bottomMargin=45,
        title=f"Exam Result - {student.full_name or student.username}",
        author="Proctored Exam Platform"
    )
    
    # Initialize elements list
    elements = []
    
    # Get custom styles
    styles = create_custom_styles()
    
    # Build document sections
    elements.extend(create_header_section(student, exam, student_exam, styles))
    elements.extend(create_info_cards(student, exam, student_exam, styles))
    elements.extend(create_performance_card(student_exam, exam, answers_with_questions, styles))
    elements.extend(create_summary_table(answers_with_questions, styles))
    elements.extend(create_detailed_questions(answers_with_questions, styles))
    elements.extend(create_proctoring_section(student_exam, styles))
    elements.extend(create_footer_section(styles))
    
    # Build PDF with custom canvas
    doc.build(elements, canvasmaker=NumberedCanvas)
    
    # Return buffer
    buffer.seek(0)
    return buffer
