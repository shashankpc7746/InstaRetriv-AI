from twilio.request_validator import RequestValidator


def is_valid_twilio_signature(
    auth_token: str,
    request_url: str,
    form_data: dict[str, str],
    signature: str,
) -> bool:
    if not auth_token.strip() or not signature.strip():
        return False

    validator = RequestValidator(auth_token)
    return validator.validate(request_url, form_data, signature)
