-- Update existing tables in place. Existing selection lists and links are retained.
ALTER TABLE public.galleries ALTER COLUMN saved_selection TYPE jsonb USING NULLIF(btrim(saved_selection::text), '')::jsonb;
ALTER TABLE public.client_selection ALTER COLUMN sent_selection TYPE jsonb USING NULLIF(btrim(sent_selection::text), '')::jsonb;
ALTER TABLE public.my_selection ALTER COLUMN selection TYPE jsonb USING NULLIF(btrim(selection::text), '')::jsonb;
ALTER TABLE public.galleries ADD CONSTRAINT saved_selection_array CHECK (saved_selection IS NULL OR jsonb_typeof(saved_selection) = 'array');
ALTER TABLE public.client_selection ADD CONSTRAINT sent_selection_array CHECK (sent_selection IS NULL OR jsonb_typeof(sent_selection) = 'array');
ALTER TABLE public.my_selection ADD CONSTRAINT my_selection_array CHECK (selection IS NULL OR jsonb_typeof(selection) = 'array');
ALTER TABLE public.client_selection ADD COLUMN gallery_id bigint;
ALTER TABLE public.my_selection ADD COLUMN gallery_id bigint;
-- Refuse ambiguous labels rather than attaching a selection to the wrong shoot.
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM public.galleries GROUP BY name || '_' || gallery HAVING count(*) > 1)
 THEN RAISE EXCEPTION 'Gallery labels are ambiguous; assign gallery IDs explicitly.'; END IF;
END $$;
UPDATE public.client_selection s SET gallery_id = g.id FROM public.galleries g WHERE s.client_gallery = g.name || '_' || g.gallery;
UPDATE public.my_selection s SET gallery_id = g.id FROM public.galleries g WHERE s.client_gallery = g.name || '_' || g.gallery;
ALTER TABLE public.client_selection ALTER COLUMN gallery_id SET NOT NULL;
ALTER TABLE public.my_selection ALTER COLUMN gallery_id SET NOT NULL;
ALTER TABLE public.client_selection ADD CONSTRAINT client_selection_gallery_fk FOREIGN KEY (gallery_id) REFERENCES public.galleries(id);
ALTER TABLE public.my_selection ADD CONSTRAINT my_selection_gallery_fk FOREIGN KEY (gallery_id) REFERENCES public.galleries(id);
ALTER TABLE public.client_selection DROP CONSTRAINT client_selection_client_gallery_key;
ALTER TABLE public.client_selection ADD PRIMARY KEY (gallery_id);
ALTER TABLE public.my_selection DROP CONSTRAINT myy_selection_pkey;
ALTER TABLE public.my_selection ADD PRIMARY KEY (gallery_id);
ALTER TABLE public.my_selection ALTER COLUMN client_gallery DROP NOT NULL;
ALTER TABLE public.client_selection ADD CONSTRAINT client_selection_stage_valid CHECK (stage IN ('choose_edits', 'wait_for_edits', 'final_edits'));
ALTER TABLE public.my_selection ADD CONSTRAINT my_selection_stage_valid CHECK (stage IN ('choose_edits', 'wait_for_edits', 'final_edits'));
ALTER TABLE public.client_selection ALTER COLUMN stage SET NOT NULL;
ALTER TABLE public.client_selection ALTER COLUMN stage SET DEFAULT 'choose_edits';
CREATE OR REPLACE FUNCTION public.sync_client_stage_to_gallery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE public.galleries SET client_stage = NEW.stage WHERE id = NEW.gallery_id AND client_stage IS DISTINCT FROM NEW.stage;
 RETURN NEW;
END $$;
CREATE TRIGGER client_stage_to_gallery AFTER INSERT OR UPDATE OF stage, gallery_id ON public.client_selection FOR EACH ROW EXECUTE FUNCTION public.sync_client_stage_to_gallery();
CREATE OR REPLACE FUNCTION public.sync_gallery_stage_to_client() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO public.client_selection (gallery_id, client_gallery, stage)
 VALUES (NEW.id, concat_ws('_', NEW.name, NEW.gallery), NEW.client_stage)
 ON CONFLICT (gallery_id) DO UPDATE SET stage = EXCLUDED.stage
 WHERE client_selection.stage IS DISTINCT FROM EXCLUDED.stage;
 RETURN NEW;
END $$;
CREATE TRIGGER gallery_stage_to_client AFTER INSERT OR UPDATE OF client_stage ON public.galleries FOR EACH ROW EXECUTE FUNCTION public.sync_gallery_stage_to_client();
