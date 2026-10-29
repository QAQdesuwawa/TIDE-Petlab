"""Creation-only provider adapter; no session, event, UI or animation writes."""
from framework.model_api import model_data_fingerprint,thaw_model_data
from framework.provider_codec import ProviderCodecParseFailureSignal
from model_packages.desktop_pet_v1.creation import VERSION,build_background_request,parse_background
from petlab_app.providers import completion_text


def measure_background(*,description,session_binding,current_position,gateway):
    system,user=build_background_request(description)
    source={"version":VERSION,"description":description}
    source_ref="background:"+model_data_fingerprint(source)
    def parse(wire):
        try:
            value=parse_background(completion_text(wire))
            model=wire.get("model")
            if not isinstance(model,str) or not model:raise ValueError("missing background model")
            return {**value,"measured_model":model}
        except (ValueError,TypeError):
            raise ProviderCodecParseFailureSignal(failure_kind="invalid_pet_background",failure_source_ref=model_data_fingerprint(wire)) from None
    result=gateway.run(session_binding=session_binding,current_position=current_position,
        operation_kind="background_initialization",source_fact_ref=source_ref,codec_contract=VERSION,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],temperature=0.0,
        require_json=True,parse=parse,source_receipt_refs=(source_ref,))
    if result.status!="parsed":raise RuntimeError("background provider did not produce a valid measured profile")
    payload=thaw_model_data(result.parsed_payload)
    receipt={"version":VERSION,"source":source,"source_fingerprint":model_data_fingerprint(source),
        "parsed_profile":payload,"provider_request_fingerprint":result.request.fingerprint,
        "provider_parse_receipt":result.parse_receipt.to_record()}
    fingerprint=model_data_fingerprint(receipt)
    return {**payload,"version":VERSION,"base_constants_version":"pet-background:"+fingerprint,
        "receipt":{**receipt,"fingerprint":fingerprint}}
