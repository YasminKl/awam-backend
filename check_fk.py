from app.services.indices.registry import INDICES_REGISTRY
from app.services.indices.calculator import CALC_FUNCTIONS

# Vérifier que chaque calc_func_name existe dans CALC_FUNCTIONS
for name, spec in INDICES_REGISTRY.items():
    assert spec.calc_func_name in CALC_FUNCTIONS, (
        f"{name} référence {spec.calc_func_name} qui n'existe pas"
    )
    print(f"✅ {name:10s} → {spec.calc_func_name:15s} "
          f"({spec.formula})")

print("\n✅ Toutes les fonctions référencées existent.")
print(f"✅ PSRI : {INDICES_REGISTRY['PSRI'].formula}")