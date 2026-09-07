"""Derive the active proofing stage from saved and sent selections."""


def selection_stage(stage, saved, sent):
    if stage == 'final_edits':
        return stage
    return 'wait_for_edits' if sent else 'choose_edits'
