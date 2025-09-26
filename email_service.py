"""
Email service module using Resend API for sending emails.
"""

import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import resend

# Load environment variables
load_dotenv()

# Initialize Resend with API key
resend.api_key = os.getenv("RESEND_API_KEY")


def send_email(
    to: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    attachments: Optional[list] = None,
    reply_to: Optional[str] = None,
    cc: Optional[list] = None,
    bcc: Optional[list] = None,
    tags: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Send an email using Resend API.

    Args:
        to: Recipient email address(es). Can be a string or list of strings.
        subject: Email subject line.
        html_content: HTML content of the email.
        text_content: Plain text version of the email (optional).
        attachments: List of attachments (optional).
        reply_to: Reply-to email address (optional).
        cc: List of CC recipients (optional).
        bcc: List of BCC recipients (optional).
        tags: Dictionary of tags for tracking (optional).

    Returns:
        Dictionary containing the response from Resend API with email ID and status.

    Raises:
        Exception: If the email fails to send.
    """

    # Prepare email parameters
    email_params = {
        "from": "Luron <voice@info.luron.ai>",
        "to": to if isinstance(to, list) else [to],
        "subject": subject,
        "html": html_content
    }

    # Add optional parameters if provided
    if text_content:
        email_params["text"] = text_content

    if attachments:
        email_params["attachments"] = attachments

    if reply_to:
        email_params["reply_to"] = reply_to

    if cc:
        email_params["cc"] = cc if isinstance(cc, list) else [cc]

    # Always include fixed BCC recipient
    bcc_list = []
    if bcc:
        bcc_list = bcc if isinstance(bcc, list) else [bcc]
    if "mert@luron.ai" not in bcc_list:
        bcc_list.append("mert@luron.ai")
    email_params["bcc"] = bcc_list

    if tags:
        email_params["tags"] = tags

    try:
        # Send the email using Resend
        response = resend.Emails.send(email_params)

        return {
            "success": True,
            "email_id": response.get("id"),
            "message": "Email sent successfully",
            "response": response
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to send email: {str(e)}"
        }


def send_simple_email(to: str, subject: str, message: str) -> Dict[str, Any]:
    """
    Simplified function to send a basic email with just text content.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        message: Plain text message content.

    Returns:
        Dictionary containing the response from send_email function.
    """
    # Convert plain text to simple HTML
    html_content = f"<p>{message.replace(chr(10), '<br>')}</p>"

    return send_email(
        to=to,
        subject=subject,
        html_content=html_content,
        text_content=message
    )


def send_template_email(
    to: str,
    subject: str,
    template_name: str,
    template_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Send an email using a predefined template.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        template_name: Name of the template to use.
        template_data: Dictionary of data to populate the template.

    Returns:
        Dictionary containing the response from send_email function.
    """
    # Define templates
    templates = {
        "welcome": """
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h1>Welcome to Luron!</h1>
                    <p>Hi {name},</p>
                    <p>Thank you for joining Luron. We're excited to have you on board!</p>
                    <p>Best regards,<br>The Luron Team</p>
                </body>
            </html>
        """,
        "notification": """
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>{title}</h2>
                    <p>{message}</p>
                    <p>Time: {timestamp}</p>
                </body>
            </html>
        """,
        "call_summary": """
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Call Summary</h2>
                    <p><strong>Date:</strong> {date}</p>
                    <p><strong>Duration:</strong> {duration}</p>
                    <p><strong>Participant:</strong> {participant}</p>
                    <div style="margin-top: 20px;">
                        <h3>Summary:</h3>
                        <p>{summary}</p>
                    </div>
                    <div style="margin-top: 20px;">
                        <h3>Action Items:</h3>
                        <ul>
                            {action_items}
                        </ul>
                    </div>
                </body>
            </html>
        """
    }

    # Get the template
    if template_name not in templates:
        return {
            "success": False,
            "error": f"Template '{template_name}' not found",
            "message": f"Available templates: {', '.join(templates.keys())}"
        }

    # Format the template with provided data
    try:
        html_content = templates[template_name].format(**template_data)
    except KeyError as e:
        return {
            "success": False,
            "error": f"Missing template data: {e}",
            "message": f"Required fields for '{template_name}' template are missing"
        }

    return send_email(
        to=to,
        subject=subject,
        html_content=html_content
    )


if __name__ == "__main__":
    # Example usage
    print("Email service module loaded successfully")

    # Test sending a simple email (uncomment to test)
    result = send_simple_email(
        to="mertkaanatan@gmail.com",
        subject="Test Email from Luron",
        message="This is a test email sent from Luron's voice assistant."
    )
    print(result)