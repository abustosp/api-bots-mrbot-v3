"""Conciliación periódica de pagos contra MercadoPago (plan 04 §7.10).

Compara el estado local (``payments``) con el recurso remoto reconsultado
(``GET`` autenticado a MercadoPago) sin editar el hecho origen: cada
diferencia genera un evento de reconciliación para revisión operativa.
Montos en centavos enteros con contraste exacto, nunca float.
"""

from __future__ import annotations


def comparar_pago(local: dict, remoto: dict) -> list[str]:
    """Devuelve las diferencias entre el pago local y el remoto.

    Códigos: ``estado_distinto``, ``monto_distinto``, ``moneda_distinta``.
    Sin diferencias devuelve lista vacía (conciliado).
    """
    from central_api.billing.facade import desde_monto_mp

    diferencias: list[str] = []
    estado_local = str(local.get("estado_local") or local.get("estado") or "")
    estado_remoto = str(remoto.get("status") or remoto.get("estado") or "")
    if estado_remoto and estado_remoto != estado_local:
        diferencias.append("estado_distinto")
    try:
        remoto_centavos = desde_monto_mp(remoto.get("transaction_amount", "0"))
    except Exception:  # noqa: BLE001 - remoto ilegible cuenta como diferencia
        diferencias.append("monto_distinto")
        remoto_centavos = None
    if remoto_centavos is not None and int(local.get("importe", 0)) != remoto_centavos:
        diferencias.append("monto_distinto")
    moneda_remota = str(remoto.get("currency_id") or remoto.get("moneda") or "")
    if moneda_remota and moneda_remota != str(local.get("moneda", "ARS")):
        diferencias.append("moneda_distinta")
    return diferencias


def conciliar_lote(
    pagos_locales: list[dict], remotos_por_referencia: dict[str, dict]
) -> dict:
    """Concilia un lote: separa conciliados, diferencias y sin contraparte.

    ``remotos_por_referencia`` mapea ``external_reference`` al recurso
    reconsultado. Los pagos sin contraparte remota quedan pendientes, nunca
    se marcan como conciliados por ausencia de evidencia.
    """
    informe: dict = {"conciliados": [], "diferencias": [], "sin_remoto": []}
    for pago in pagos_locales:
        referencia = str(pago.get("externo_id") or pago.get("external_reference") or "")
        remoto = remotos_por_referencia.get(referencia)
        if remoto is None:
            informe["sin_remoto"].append(pago.get("id"))
            continue
        difs = comparar_pago(pago, remoto)
        if difs:
            informe["diferencias"].append({"payment_id": pago.get("id"), "difs": difs})
        else:
            informe["conciliados"].append(pago.get("id"))
    return informe


__all__ = ["comparar_pago", "conciliar_lote"]
