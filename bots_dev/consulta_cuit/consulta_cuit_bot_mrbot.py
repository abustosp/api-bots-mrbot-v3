import requests
import os

API_CONSULTA_CUIT_URL = os.getenv("API_CONSULTA_CUIT_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia/")
API_CONSULTA_CUIT_MASIVA_URL = os.getenv("API_CONSULTA_CUIT_MASIVA_URL", "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia_masiva/")
API_CONSULTA_CUIT_USUARIO = os.getenv("API_CONSULTA_CUIT_USUARIO")
API_CONSULTA_CUIT_KEY = os.getenv("API_CONSULTA_CUIT_KEY")

def consulta_cuit(cuit: str) -> dict:
    """
    Consulta el CUIT en la API externa de constancias de inscripción.

    :param cuit: CUIT a consultar.
    :return: Diccionario con la respuesta de la API.
    """
    params = {
        "cuit": cuit
    }
    if API_CONSULTA_CUIT_USUARIO:
        params["usuario"] = API_CONSULTA_CUIT_USUARIO
    if API_CONSULTA_CUIT_KEY:
        params["api_key"] = API_CONSULTA_CUIT_KEY

    response = requests.get(API_CONSULTA_CUIT_URL, params=params)

    response.raise_for_status()
    return response.json()
        
def consulta_cuit_masiva(cuits: list) -> dict:
    """
    Consulta múltiples CUITs en la API externa de constancias de inscripción.

    :param cuits: Lista de CUITs a consultar.
    :return: Diccionario con la respuesta de la API.
    """
    payload = {
        "cuits": cuits
    }
    params = {}
    if API_CONSULTA_CUIT_USUARIO:
        params["usuario"] = API_CONSULTA_CUIT_USUARIO
    if API_CONSULTA_CUIT_KEY:
        params["api_key"] = API_CONSULTA_CUIT_KEY

    response = requests.post(API_CONSULTA_CUIT_MASIVA_URL, params=params, json=payload)

    response.raise_for_status()
    return response.json()