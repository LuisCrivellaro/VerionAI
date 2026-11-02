-- Esquema do ledger do harness da Fase 0 (spec 3.7). Versão 1.
-- Aplicado por verion.ledger.repo.connect quando PRAGMA user_version = 0.
-- IF NOT EXISTS torna a aplicação idempotente caso dois processos abram um banco novo ao mesmo tempo.
-- Reversão: repo.drop_all(conn) remove tudo e volta user_version para 0 (só testes).

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    property_id   TEXT    NOT NULL,
    method        TEXT    NOT NULL,
    script_path   TEXT    NOT NULL,
    script_sha256 TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,                 -- ISO 8601 UTC
    finished_at   TEXT,
    notes         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attempts (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    scene_index      INTEGER NOT NULL CHECK (scene_index BETWEEN 1 AND 12),
    attempt_no       INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 3),
    method           TEXT    NOT NULL,
    provider         TEXT    NOT NULL,
    model            TEXT    NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('pending', 'ok', 'error')),
    target_s         REAL    NOT NULL,
    reserved_brl     REAL    NOT NULL CHECK (reserved_brl >= 0),
    cost_brl         REAL    NOT NULL CHECK (cost_brl >= 0),
    cost_is_estimate INTEGER NOT NULL CHECK (cost_is_estimate IN (0, 1)),
    units            REAL    NOT NULL,
    unit_kind        TEXT    NOT NULL,
    clip_path        TEXT,
    duration_s       REAL,
    wall_time_s      REAL,
    provider_job_id  TEXT,
    error            TEXT,
    created_at       TEXT    NOT NULL,
    finished_at      TEXT,
    UNIQUE (run_id, scene_index, attempt_no),
    -- toda falha mantém a reserva como custo mínimo
    CHECK (status <> 'error' OR cost_brl >= reserved_brl),
    -- enquanto pendente, o gasto contado é exatamente a reserva
    CHECK (status <> 'pending' OR cost_brl = reserved_brl)
);

CREATE INDEX IF NOT EXISTS attempts_by_scene ON attempts (run_id, scene_index);

CREATE TABLE IF NOT EXISTS qc_reviews (
    id           INTEGER PRIMARY KEY,
    attempt_id   INTEGER NOT NULL UNIQUE REFERENCES attempts(id),
    verdict      TEXT    NOT NULL CHECK (verdict IN ('aprovada', 'refazer', 'rejeitada')),
    reviewer     TEXT    NOT NULL CHECK (reviewer IN ('humano', 'agente')),
    defects_json TEXT    NOT NULL,
    reviewed_at  TEXT    NOT NULL,
    -- só humano aprova (skill verion-fidelity-qc)
    CHECK (verdict <> 'aprovada' OR reviewer = 'humano')
);

CREATE TABLE IF NOT EXISTS authorizations (
    id         INTEGER PRIMARY KEY,
    amount_brl REAL NOT NULL CHECK (amount_brl > 0),
    reference  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Invariante: nenhum comando reduz o gasto registrado. Tentativa liquidada (ok/error) é imutável
-- e nenhuma tentativa pode ser apagada.
CREATE TRIGGER IF NOT EXISTS attempts_settled_immutable
BEFORE UPDATE ON attempts
WHEN OLD.status <> 'pending'
BEGIN
    SELECT RAISE(ABORT, 'tentativa liquidada e imutavel');
END;

-- A reserva e a identidade da linha nunca mudam, nem enquanto pending: budget valida o SpendToken
-- contra estes campos e calcula o preço a partir de method. Zerar reserved_brl (e cost_brl junto)
-- passaria pelos CHECKs e reduziria o gasto registrado.
CREATE TRIGGER IF NOT EXISTS attempts_reservation_immutable
BEFORE UPDATE OF reserved_brl, method, run_id, scene_index, attempt_no ON attempts
WHEN NEW.reserved_brl IS NOT OLD.reserved_brl
  OR NEW.method IS NOT OLD.method
  OR NEW.run_id IS NOT OLD.run_id
  OR NEW.scene_index IS NOT OLD.scene_index
  OR NEW.attempt_no IS NOT OLD.attempt_no
BEGIN
    SELECT RAISE(ABORT, 'reserva e identidade da tentativa sao imutaveis');
END;

CREATE TRIGGER IF NOT EXISTS attempts_no_delete
BEFORE DELETE ON attempts
BEGIN
    SELECT RAISE(ABORT, 'tentativas nao podem ser apagadas');
END;

PRAGMA user_version = 1;
