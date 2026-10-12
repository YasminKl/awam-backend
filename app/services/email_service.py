import resend
from app.core.config import RESEND_API_KEY, EMAIL_FROM

resend.api_key = RESEND_API_KEY


def send_reset_password_email(to_email: str, reset_link: str) -> None:
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
        <h2 style="color: #1a1a1a;">Réinitialisation de votre mot de passe</h2>
        <p style="color: #333; font-size: 15px; line-height: 1.5;">
            Vous avez demandé à réinitialiser votre mot de passe sur AWAM.
            Cliquez sur le bouton ci-dessous pour choisir un nouveau mot de passe.
        </p>
        <div style="text-align: center; margin: 32px 0;">
            <a href="{reset_link}"
               style="background-color: #d97757; color: white; padding: 12px 24px;
                      border-radius: 8px; text-decoration: none; font-weight: bold;">
                Réinitialiser mon mot de passe
            </a>
        </div>
        <p style="color: #d97757; font-size: 13px; font-weight: bold;">
            ⏱️ Ce lien expire dans 5 minutes.
        </p>
        <p style="color: #888; font-size: 12px; line-height: 1.5;">
            Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet e-mail.
        </p>
    </div>
    """

    try:
        resend.Emails.send({
            "from": f"AWAM <{EMAIL_FROM}>",
            "to": [to_email],
            "subject": "Réinitialisation de votre mot de passe AWAM",
            "html": html_content,
        })
        print(f"✅ Email de réinitialisation envoyé à {to_email}")
    except Exception as e:
        print(f"🔴 ERREUR lors de l'envoi de l'email à {to_email} : {e}")
        raise


def send_agenda_reminder_email(to_email: str, event_title: str, event_datetime_str: str) -> None:
    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
        <h2 style="color: #1a1a1a;">Rappel : {event_title}</h2>
        <p style="color: #333; font-size: 15px; line-height: 1.5;">
            Vous avez un événement à venir sur AWAM :
        </p>
        <div style="background-color: #f5f5f0; border-radius: 8px; padding: 16px; margin: 20px 0;">
            <p style="margin: 0; font-weight: bold; color: #1a1a1a;">{event_title}</p>
            <p style="margin: 4px 0 0; color: #666; font-size: 14px;">{event_datetime_str}</p>
        </div>
        <p style="color: #888; font-size: 12px; line-height: 1.5;">
            Ceci est un rappel automatique envoyé par AWAM.
        </p>
    </div>
    """

    try:
        resend.Emails.send({
            "from": f"AWAM <{EMAIL_FROM}>",
            "to": [to_email],
            "subject": f"Rappel AWAM : {event_title}",
            "html": html_content,
        })
        print(f"✅ Email de rappel agenda envoyé à {to_email}")
    except Exception as e:
        print(f"🔴 ERREUR lors de l'envoi du rappel agenda à {to_email} : {e}")
        raise