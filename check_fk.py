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