"""Catálogo de estrategias de respaldo de PostgreSQL usadas en el proyecto.

La columna "no protege" es la más importante: evita la sorpresa después del incidente.
"""

STRATEGIES = [
    {
        "id": "snapshot",
        "name": "Punto de restauración (COPY → CSV + ZIP)",
        "tool": "COPY ... TO STDOUT desde la app",
        "where": "Botón en esta página · se guarda en Neon (esquema vault)",
        "frequency": "Bajo demanda (antes de un cambio riesgoso)",
        "rpo": "El momento del clic", "rto": "Segundos",
        "protects": "Datos de las tablas de negocio, con huella md5 por tabla. Restauración atómica: si la huella no coincide, se revierte.",
        "not_protects": "Esquema, índices, funciones y usuarios. Vive en el mismo servidor: si se pierde Neon, se pierde también.",
        "status": "live",
    },
    {
        "id": "pg_dump",
        "name": "Respaldo lógico completo cifrado",
        "tool": "pg_dump -Fc + GPG AES-256",
        "where": "GitHub Actions (backup.yml) · artefacto fuera del proveedor, 30 días",
        "frequency": "Diario 06:00 UTC + manual",
        "rpo": "≤ 24 h", "rto": "Minutos",
        "protects": "Toda la base (esquema + datos) portable a otro servidor o versión. Sobrevive a la pérdida total de la cuenta de Neon.",
        "not_protects": "Los cambios hechos después del último respaldo. Roles y configuración del servidor (van aparte).",
        "status": "live",
    },
    {
        "id": "pitr",
        "name": "PITR — recuperación a un punto en el tiempo",
        "tool": "Historial WAL de Neon (branch en un instante)",
        "where": "Neon · ventana de 6 h en el plan gratuito",
        "frequency": "Continuo",
        "rpo": "Segundos", "rto": "Minutos",
        "protects": "Errores humanos recientes (DELETE sin WHERE, migración fallida): se vuelve al segundo previo.",
        "not_protects": "Errores detectados después de la ventana de retención. Una caída del propio proveedor.",
        "status": "provider",
    },
    {
        "id": "basebackup",
        "name": "Respaldo físico completo",
        "tool": "pg_basebackup",
        "where": "Demo local (scripts/demo_incremental.ps1)",
        "frequency": "Semanal (típico)",
        "rpo": "Desde el último completo", "rto": "Rápido en bases grandes",
        "protects": "El clúster entero bloque a bloque; base para PITR con archivado de WAL.",
        "not_protects": "No es portable entre versiones mayores; no permite restaurar una sola tabla.",
        "status": "demo",
    },
    {
        "id": "incremental",
        "name": "Respaldo físico incremental (PostgreSQL 17)",
        "tool": "pg_basebackup --incremental + pg_combinebackup",
        "where": "Demo local (scripts/demo_incremental.ps1)",
        "frequency": "Diario sobre el completo semanal",
        "rpo": "Desde el último incremental", "rto": "Combinar + arrancar",
        "protects": "Solo los bloques cambiados: mucho menos espacio y tiempo que repetir el completo.",
        "not_protects": "Depende de TODA la cadena: si falta un incremental o el completo base, no se puede reconstruir.",
        "status": "demo",
    },
]
