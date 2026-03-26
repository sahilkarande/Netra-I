"""
Email Utility for OTP and Notifications
Includes console fallback for development without SMTP
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv

load_dotenv()


def send_otp_email(email, otp, username, role="student"):
    """
    Send OTP to user's email with beautiful HTML template
    Falls back to console display if SMTP is not configured
    
    Args:
        email (str): Recipient email address
        otp (str): 6-digit OTP code
        username (str): User's username (PRN or Employee ID)
        role (str): User role ('student' or 'faculty')
    
    Returns:
        bool: True if successful (including console fallback)
    """
    
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', 587))
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')
    from_email = os.getenv('FROM_EMAIL', smtp_username)
    
    # Check if email is configured
    if not smtp_username or not smtp_password:
        # Display OTP in console if email not configured
        print("\n" + "="*70)
        print("📧 OTP EMAIL (SMTP not configured - displaying in console)")
        print("="*70)
        print(f"📧 To: {email}")
        print(f"👤 Username: {username}")
        print(f"👔 Role: {role.capitalize()}")
        print(f"")
        print(f"🔐 YOUR OTP CODE: {otp}")
        print(f"")
        print(f"⏰ Valid for: 10 minutes")
        print(f"")
        print("💡 To enable email sending, add to .env:")
        print("   SMTP_SERVER=smtp.gmail.com")
        print("   SMTP_PORT=587")
        print("   SMTP_USERNAME=your-email@gmail.com")
        print("   SMTP_PASSWORD=your-gmail-app-password")
        print("   FROM_EMAIL=your-email@gmail.com")
        print("="*70 + "\n")
        return True
    
    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Your OTP - Proctored Exam Platform'
        msg['From'] = from_email
        msg['To'] = email
        
        # HTML content with beautiful styling
        html = f"""
        <html>
          <head>
            <style>
              body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                background-color: #f5f5f5; 
                margin: 0; 
                padding: 0; 
              }}
              .container {{ 
                max-width: 600px; 
                margin: 40px auto; 
                background-color: white; 
                border-radius: 12px; 
                box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
                overflow: hidden; 
              }}
              .header {{ 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                padding: 30px; 
                text-align: center; 
              }}
              .header h1 {{ 
                color: white; 
                margin: 0; 
                font-size: 28px; 
              }}
              .header p {{ 
                color: rgba(255,255,255,0.9); 
                margin: 10px 0 0 0; 
                font-size: 14px; 
              }}
              .content {{ 
                padding: 40px 30px; 
              }}
              .greeting {{ 
                color: #333; 
                font-size: 18px; 
                margin-bottom: 20px; 
              }}
              .otp-box {{ 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                padding: 25px; 
                text-align: center; 
                border-radius: 8px; 
                margin: 30px 0; 
              }}
              .otp-code {{ 
                color: white; 
                font-size: 42px; 
                font-weight: bold; 
                letter-spacing: 8px; 
                margin: 0; 
                font-family: 'Courier New', monospace; 
              }}
              .info {{ 
                background-color: #f8f9fa; 
                padding: 20px; 
                border-radius: 8px; 
                margin: 20px 0; 
                border-left: 4px solid #667eea; 
              }}
              .info-item {{ 
                color: #555; 
                font-size: 14px; 
                margin: 8px 0; 
              }}
              .warning {{ 
                color: #e74c3c; 
                font-size: 13px; 
                margin-top: 20px; 
                padding: 15px; 
                background-color: #fee; 
                border-radius: 6px; 
              }}
              .footer {{ 
                text-align: center; 
                padding: 20px; 
                background-color: #f8f9fa; 
                color: #999; 
                font-size: 12px; 
              }}
              .icon {{ 
                font-size: 48px; 
                margin-bottom: 10px; 
              }}
            </style>
          </head>
          <body>
            <div class="container">
              <div class="header">
                <div class="icon">🎓</div>
                <h1>Proctored Exam Platform</h1>
                <p>Secure Online Examination System</p>
              </div>
              
              <div class="content">
                <div class="greeting">
                  Hello <strong>{username}</strong>,
                </div>
                
                <p style="color: #666; font-size: 15px; line-height: 1.6;">
                  Thank you for registering with our Proctored Exam Platform. To complete your 
                  registration, please verify your email address using the One-Time Password (OTP) below:
                </p>
                
                <div class="otp-box">
                  <p class="otp-code">{otp}</p>
                </div>
                
                <div class="info">
                  <div class="info-item">✓ <strong>Role:</strong> {role.capitalize()}</div>
                  <div class="info-item">⏰ <strong>Valid for:</strong> 10 minutes</div>
                  <div class="info-item">🔒 <strong>Security:</strong> Do not share this OTP with anyone</div>
                </div>
                
                <p style="color: #666; font-size: 14px; margin-top: 25px;">
                  Enter this OTP on the verification page to activate your account and start using the platform.
                </p>
                
                <div class="warning">
                  ⚠️ <strong>Important:</strong> If you didn't request this OTP, please ignore this email. 
                  Your account will not be activated without OTP verification.
                </div>
              </div>
              
              <div class="footer">
                <p>This is an automated message from Proctored Exam Platform</p>
                <p>Please do not reply to this email</p>
                <p style="margin-top: 10px;">© 2024 Exam Platform. All rights reserved.</p>
              </div>
            </div>
          </body>
        </html>
        """
        
        # Plain text alternative
        text = f"""
        PROCTORED EXAM PLATFORM
        =======================
        
        Hello {username},
        
        Your One-Time Password (OTP) for registration is:
        
        {otp}
        
        Role: {role.capitalize()}
        Valid for: 10 minutes
        
        Enter this OTP on the verification page to complete your registration.
        
        IMPORTANT: If you didn't request this OTP, please ignore this email.
        
        ---
        This is an automated message. Please do not reply.
        © 2024 Proctored Exam Platform
        """
        
        part1 = MIMEText(text, 'plain')
        part2 = MIMEText(html, 'html')
        
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        print(f"✅ OTP email sent successfully to {email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        print(f"\n📧 FALLBACK - Displaying OTP in console:")
        print(f"Email: {email}")
        print(f"Username: {username}")
        print(f"OTP: {otp}")
        print(f"Valid for: 10 minutes\n")
        return True  # Return True anyway so registration can continue


def send_result_notification(email, username, exam_title, score, passed):
    """
    Send exam result notification email
    
    Args:
        email (str): Student email
        username (str): Student username
        exam_title (str): Exam title
        score (float): Score percentage
        passed (bool): Whether student passed
    
    Returns:
        bool: True if successful
    """
    
    smtp_username = os.getenv('SMTP_USERNAME')
    smtp_password = os.getenv('SMTP_PASSWORD')
    
    if not smtp_username or not smtp_password:
        print(f"📧 Result notification (console): {username} - {exam_title}: {score}%")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['Subject'] = f'Exam Result: {exam_title}'
        msg['From'] = os.getenv('FROM_EMAIL', smtp_username)
        msg['To'] = email
        
        status_color = '#2ecc71' if passed else '#e74c3c'
        status_text = 'PASSED ✓' if passed else 'FAILED ✗'
        
        html = f"""
        <html>
          <body style="font-family: Arial; padding: 20px; background-color: #f5f5f5;">
            <div style="max-width: 600px; margin: 0 auto; background-color: white; border-radius: 10px; 
                        overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
              <div style="background-color: {status_color}; color: white; padding: 30px; text-align: center;">
                <h1 style="margin: 0; font-size: 32px;">{status_text}</h1>
              </div>
              <div style="padding: 30px;">
                <h2>Hello {username},</h2>
                <p>Your exam results for <strong>{exam_title}</strong> are now available.</p>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                  <h3 style="margin-top: 0;">Score: {score:.2f}%</h3>
                </div>
                <p>Log in to your account to view detailed results and download your report.</p>
              </div>
              <div style="background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 12px;">
                <p>© 2024 Proctored Exam Platform</p>
              </div>
            </div>
          </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP(os.getenv('SMTP_SERVER', 'smtp.gmail.com'), 
                         int(os.getenv('SMTP_PORT', 587))) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        print(f"✅ Result notification sent to {email}")
        return True
        
    except Exception as e:
        print(f"❌ Error sending result notification: {str(e)}")
        return False