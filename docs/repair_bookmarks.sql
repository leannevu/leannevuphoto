-- Run inside a transaction. This temporary function leaves other columns alone.
-- It unwraps legacy JSON strings without dropping a saved photo reference.
CREATE OR REPLACE FUNCTION pg_temp.normalized_bookmark(raw_value text)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    decoded jsonb;
    depth integer;
BEGIN
    IF raw_value IS NULL THEN RETURN NULL; END IF;
    -- Repeatedly encoded nulls can grow to hundreds of MB. Remove only the
    -- surrounding JSON string quotes/escapes before allocating a JSON parser.
    IF lower(btrim(replace(raw_value, chr(92), ''), chr(34) || ' ')) = 'null' THEN
        RETURN 'null'::jsonb;
    END IF;
    FOR depth IN 1..64 LOOP
        decoded := raw_value::jsonb;
        IF jsonb_typeof(decoded) <> 'string' THEN
            IF decoded = 'null'::jsonb OR
               (jsonb_typeof(decoded) = 'object' AND
                jsonb_typeof(decoded->'id') = 'string' AND
                jsonb_typeof(decoded->'name') = 'string') THEN
                RETURN decoded;
            END IF;
            RAISE EXCEPTION 'Invalid bookmark; transaction must be rolled back';
        END IF;
        raw_value := decoded #>> '{}';
    END LOOP;
    RAISE EXCEPTION 'Too many bookmark encoding layers; transaction must be rolled back';
END;
$$;

ALTER TABLE public.emails ALTER COLUMN bookmark DROP DEFAULT;
ALTER TABLE public.emails ALTER COLUMN bookmark TYPE jsonb
    USING pg_temp.normalized_bookmark(bookmark::text);
