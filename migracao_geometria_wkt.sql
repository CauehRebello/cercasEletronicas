-- Migração incremental — coluna geometria_wkt em cercas_central
-- Feature de Mapa/Visualização v4 (DEC-V4-18/FAT-326, DEC-V4-21)
--
-- Aplicar no servidor central (10.2.15.3), sem recriar a tabela:
--   psql -h 10.2.15.3 -U <usuario> -d <banco> -f migracao_geometria_wkt.sql
--
-- Nullable: não quebra linhas existentes (histórico é preenchido depois
-- via backfill_geometria_wkt.py — DEC-V4-22).

ALTER TABLE public.cercas_central ADD COLUMN IF NOT EXISTS geometria_wkt TEXT;
