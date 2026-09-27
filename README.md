#démarrage de celery worker
celery -A app.celery_app worker --loglevel=info --pool=solo    

#démarrage de celery beat 
celery -A app.celery_app beat --loglevel=info


#Correction de alembic
SELECT * FROM alembic_version;

DELETE FROM alembic_version;

INSERT INTO alembic_version (version_num) VALUES ('716602d84a0a'); //a changer avec value qui a le probleme

SELECT * FROM alembic_version;

#activer les fichiers dll* dans postgre
Get-ChildItem "C:\Program Files\PostgreSQL\16\lib\*.dll" | Select-Object Name, Length, LastWriteTime


#vide le cache python
Get-ChildItem -Path . -Filter __pycache__ -Recurse | Remove-Item -Recurse -Force

#pour vérifier la table parcel, si elle contient les nouvelles données entrées
py -c "from app.db.database import SessionLocal; from sqlalchemy import text; db = SessionLocal(); rows = db.execute(text('SELECT id, name, culture_type, soil_type, irrigation_type, area_ha FROM parcel ORDER BY id DESC LIMIT 3')).fetchall(); [print(f'id={r.id} | {r.name} | cult={r.culture_type} | sol={r.soil_type} | irr={r.irrigation_type} | area={r.area_ha}') for r in rows]; db.close()"

#pour verifier la table farm, si les nouvelles données entrées (nouvelles, update) sont introduites ou pas
py -c "from app.db.database import SessionLocal; from sqlalchemy import text; db = SessionLocal(); rows = db.execute(text('SELECT id, name, location, latitude, longitude FROM farm ORDER BY id')).fetchall(); [print(f'id={r.id} | {r.name:20} | {r.location} | lat={r.latitude} | lon={r.longitude}') for r in rows]; db.close()"


#pour tester les modification ou suppression des parcelles (passer la 2 fois, une fois avant les changement et autre apres)
py -c "from app.db.database import SessionLocal; from sqlalchemy import text; db = SessionLocal(); rows = db.execute(text('SELECT id, name, culture_type, soil_type, irrigation_type, area_ha FROM parcel ORDER BY id DESC LIMIT 5')).fetchall(); [print(f'id={r.id} | {r.name} | {r.culture_type} | {r.soil_type} | {r.irrigation_type} | {r.area_ha} ha') for r in rows]; db.close()"


#pour voir les bandes
Select-String -Path app\services\sentinel_service.py -Pattern "_mask_clouds_with_scl|SCL_MASK_VALUES"


#pour tester; generation d'une parcelle (juste change id voulu)
py -c "from app.tasks.parcel_tasks import generate_parcel_rasters; print(generate_parcel_rasters(61))" 


#pour voir le deflet (la compression d'image)
Select-String -Path app\services\sentinel_service.py -Pattern '"compress": "deflate"'


#si tu veux etre sûr que les fichiers raster passent correctement,
#dans un fichier 

"""
Test l'API /api/raster/current en simulant un appel interne.
"""
from app.db.database import SessionLocal
from sqlalchemy import text
from app.services.b2_storage import generate_presigned_url

db = SessionLocal()

# Récupérer la dernière parcelle
parcel = db.execute(text("""
    SELECT id FROM parcel ORDER BY id DESC LIMIT 1
""")).fetchone()

if not parcel:
    print("❌ Aucune parcelle")
    db.close()
    exit(1)

parcel_id = parcel.id
print(f"Parcelle testée : {parcel_id}\n")

# Pour chaque type de raster, simuler la logique de l'API
for raster_type in ["mask", "sentinel_rgb", "sentinel_ndvi"]:
    row = db.execute(text("""
        SELECT b2_key, bbox_west, bbox_south, bbox_east, bbox_north
        FROM raster
        WHERE parcel_id = :pid AND raster_type = :rt
        ORDER BY COALESCE(scene_date, created_at::date) DESC, created_at DESC
        LIMIT 1
    """), {"pid": parcel_id, "rt": raster_type}).fetchone()

    if not row:
        print(f"❌ {raster_type} : AUCUN raster en base")
        continue

    try:
        signed_url = generate_presigned_url(row.b2_key, expires_in=3600)
        print(f"✅ {raster_type}")
        print(f"   b2_key : {row.b2_key}")
        print(f"   URL    : {signed_url[:100]}...")
        print(f"   bbox   : ({row.bbox_west}, {row.bbox_south}, {row.bbox_east}, {row.bbox_north})")
    except Exception as e:
        print(f"❌ {raster_type} : erreur signature B2 : {e}")
    print()

db.close()

#run le fichier



#code pour nettoyer le code (orphan)
"""
Supprime les rasters orphelins (parcel_id NULL).
À lancer UNE FOIS après le refactor Sentinel-2.
"""
from app.db.database import SessionLocal
from app.services.b2_storage import get_b2_client, B2_BUCKET_NAME
from sqlalchemy import text


def delete_all_versions(client, key: str) -> int:
    """Supprime toutes les versions d'une clé B2 (versions + markers)."""
    paginator = client.get_paginator("list_object_versions")
    deleted = 0
    for page in paginator.paginate(Bucket=B2_BUCKET_NAME, Prefix=key):
        to_delete = []
        for v in page.get("Versions", []):
            if v["Key"] == key:
                to_delete.append({"Key": key, "VersionId": v["VersionId"]})
        for m in page.get("DeleteMarkers", []):
            if m["Key"] == key:
                to_delete.append({"Key": key, "VersionId": m["VersionId"]})
        if to_delete:
            client.delete_objects(
                Bucket=B2_BUCKET_NAME,
                Delete={"Objects": to_delete, "Quiet": True},
            )
            deleted += len(to_delete)
    return deleted


def main():
    db = SessionLocal()
    client = get_b2_client()

    # Récupérer tous les rasters avec parcel_id NULL
    rows = db.execute(text("""
        SELECT id, parcel_id, raster_type, b2_key
        FROM raster
        WHERE parcel_id IS NULL
        ORDER BY id
    """)).fetchall()

    if not rows:
        print("✅ Aucun raster orphelin à supprimer")
        db.close()
        return

    print(f"🧹 {len(rows)} rasters orphelins à supprimer\n")

    for r in rows:
        key_str = r.b2_key or "NULL"
        print(f"  id={r.id} | {key_str}")

        if r.b2_key:
            try:
                n = delete_all_versions(client, r.b2_key)
                print(f"         → {n} version(s) supprimée(s) dans B2")
            except Exception as e:
                print(f"         ⚠️ B2: {type(e).__name__}: {e}")
        else:
            print(f"         ⚠️ b2_key NULL, rien à supprimer dans B2")

        # Supprimer de la base
        db.execute(text("DELETE FROM raster WHERE id = :id"), {"id": r.id})

    db.commit()
    db.close()
    print(f"\n✅ {len(rows)} rasters orphelins supprimés")


if __name__ == "__main__":
    main()



#code pour clean les duplication
"""
Supprime les doublons de rasters.

Pour chaque combinaison (parcel_id, raster_type, scene_date),
on garde UNIQUEMENT le raster le plus récent (id le plus grand),
et on supprime les autres (en base ET dans B2).
"""
from app.db.database import SessionLocal
from app.services.b2_storage import get_b2_client, B2_BUCKET_NAME
from sqlalchemy import text


def delete_all_versions(client, key: str) -> int:
    """Supprime toutes les versions d'une clé B2."""
    paginator = client.get_paginator("list_object_versions")
    deleted = 0
    for page in paginator.paginate(Bucket=B2_BUCKET_NAME, Prefix=key):
        to_delete = []
        for v in page.get("Versions", []):
            if v["Key"] == key:
                to_delete.append({"Key": key, "VersionId": v["VersionId"]})
        for m in page.get("DeleteMarkers", []):
            if m["Key"] == key:
                to_delete.append({"Key": key, "VersionId": m["VersionId"]})
        if to_delete:
            client.delete_objects(
                Bucket=B2_BUCKET_NAME,
                Delete={"Objects": to_delete, "Quiet": True},
            )
            deleted += len(to_delete)
    return deleted


def main():
    db = SessionLocal()
    client = get_b2_client()

    # Identifier les doublons avec ROW_NUMBER()
    # Partition par (parcel_id, raster_type, scene_date)
    # Tri par id DESC → rn=1 = plus récent, rn>1 = doublon
    rows = db.execute(text("""
        SELECT id, parcel_id, raster_type, scene_date, b2_key,
               ROW_NUMBER() OVER (
                   PARTITION BY parcel_id,
                                raster_type,
                                COALESCE(scene_date, '1970-01-01'::date)
                   ORDER BY id DESC
               ) AS rn
        FROM raster
        WHERE parcel_id IS NOT NULL
        ORDER BY parcel_id DESC, raster_type, id DESC
    """)).fetchall()

    # Filtrer les doublons (rn > 1)
    duplicates = [r for r in rows if r.rn > 1]

    if not duplicates:
        print("✅ Aucun doublon à supprimer")
        db.close()
        return

    print(f"🧹 {len(duplicates)} doublons à supprimer\n")

    for r in duplicates:
        key_str = r.b2_key or "NULL"
        date_str = str(r.scene_date)[:10] if r.scene_date else "—"
        print(f"  id={r.id} | parcel={r.parcel_id} | {r.raster_type} | {date_str} | {key_str}")

        if r.b2_key:
            try:
                n = delete_all_versions(client, r.b2_key)
                print(f"         → {n} version(s) supprimée(s) dans B2")
            except Exception as e:
                print(f"         ⚠️ B2: {type(e).__name__}: {e}")

        db.execute(text("DELETE FROM raster WHERE id = :id"), {"id": r.id})

    db.commit()
    db.close()
    print(f"\n✅ {len(duplicates)} doublons supprimés")


if __name__ == "__main__":
    main()




