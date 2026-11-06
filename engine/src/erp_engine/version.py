"""Engine and module versions (Blueprint §6.5). Bump a module version whenever any of its
outputs can change; golden tests pin outputs to these versions."""

ENGINE_VERSION = "0.1.0"

MODULE_VERSIONS: dict[str, str] = {
    "classification": "1.0.0",
    "forecast": "1.0.0",
    "flows": "1.0.0",
    "terminal": "1.0.0",
    "discounting": "1.0.0",
    "bridge": "1.0.0",
    "rollforward": "1.0.0",
    "dilution": "1.0.0",
    "wacc": "1.0.0",
}
