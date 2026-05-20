"""
Servicio de alertas por WhatsApp vía Twilio.
Se activa solo si las variables de entorno TWILIO_* están configuradas.
Sin ellas todas las llamadas son no-op y no causan errores.

Variables de entorno requeridas:
    TWILIO_ACCOUNT_SID  — SID de la cuenta Twilio
    TWILIO_AUTH_TOKEN   — Auth token de Twilio
    TWILIO_FROM_NUMBER  — Número sandbox Twilio (ej: whatsapp:+14155238886)
"""
import os
import logging

logger = logging.getLogger(__name__)

_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "")
_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")  # formato: whatsapp:+14155238886
_DISPONIBLE  = bool(_ACCOUNT_SID and _AUTH_TOKEN and _FROM_NUMBER)


def whatsapp_disponible() -> bool:
    """Retorna True si Twilio está configurado correctamente."""
    return _DISPONIBLE


def enviar_alerta_whatsapp(to_number: str, mensaje: str) -> bool:
    """
    Envía un mensaje de WhatsApp al número indicado.
    to_number: número internacional sin el prefijo 'whatsapp:', ej '+51987654321'.
    Retorna True si el envío fue exitoso.
    """
    if not _DISPONIBLE:
        logger.debug("WhatsApp no configurado — alerta omitida para %s", to_number)
        return False

    try:
        from twilio.rest import Client
        client  = Client(_ACCOUNT_SID, _AUTH_TOKEN)
        to_fmt  = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        message = client.messages.create(
            body=f"🐟 FishRoute Pro\n{mensaje}",
            from_=_FROM_NUMBER,
            to=to_fmt,
        )
        logger.info("WhatsApp enviado a %s — SID: %s", to_number, message.sid)
        return True
    except ImportError:
        logger.warning("twilio no instalado — agrega 'twilio' a requirements.txt")
        return False
    except Exception as exc:
        logger.warning("Error al enviar WhatsApp a %s: %s", to_number, exc)
        return False


def notificar_alerta_maritima(
    numero:  str,
    nivel:   str,
    mensaje: str,
    puerto:  str,
) -> bool:
    """
    Notifica una alerta marítima a un capitán por WhatsApp.
    nivel: 'ROJO' | 'AMARILLO' | 'VERDE'
    """
    icono = {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢"}.get(nivel, "⚠️")
    texto = (
        f"{icono} Alerta {nivel} — Puerto {puerto}\n"
        f"{mensaje}\n"
        "Consulta más detalles en FishRoute Pro."
    )
    return enviar_alerta_whatsapp(numero, texto)
