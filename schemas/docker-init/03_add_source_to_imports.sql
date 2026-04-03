-- Add source column to fidelity_imports if it doesn't exist
ALTER TABLE fidelity_imports
    ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'fidelity';
