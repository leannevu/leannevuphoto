import unittest
from services.database import gallery_access, published


class PublicationTests(unittest.TestCase):
    def row(self, **changes):
        row = dict(id=7, gallery='Wedding', date=None, folder_url='https://drive.google.com/drive/folders/proofs',
                   process='google', selection_stage='wait_for_edits', client_stage='wait_for_edits',
                   production_link='https://drive.google.com/drive/folders/client',
                   photographer_stage='wait_for_edits', photographer_link='https://drive.google.com/drive/folders/photographer')
        return dict(row, **changes)

    def test_both_publication_conditions_are_required(self):
        for stage in ('choose_edits', 'wait_for_edits', 'final_edits', None):
            for link in (None, '', '  ', 'https://drive.google.com/drive/folders/ready'):
                self.assertEqual(published(stage, link), stage == 'final_edits' and bool(link and link.strip()))

    def test_unpublished_links_never_enter_client_access(self):
        access = gallery_access([self.row()])
        self.assertEqual(list(access), ['gallery:7:proofs'])
        self.assertNotIn('production_link', access['gallery:7:proofs'])
        self.assertEqual(access['gallery:7:proofs']['source_url'], self.row()['folder_url'])

    def test_publication_is_independent_for_each_selection(self):
        for client, photographer in ((False, False), (True, False), (False, True), (True, True)):
            access = gallery_access([self.row(selection_stage='final_edits' if client else 'wait_for_edits',
                                             photographer_stage='final_edits' if photographer else 'wait_for_edits')])
            self.assertEqual('gallery:7:client' in access, client)
            self.assertEqual('gallery:7:photographer' in access, photographer)
            self.assertNotEqual(access['gallery:7:proofs']['stage'], 'final_edits')

    def test_shared_links_and_repeated_names_keep_separate_identities(self):
        first = self.row(selection_stage='final_edits', photographer_stage='final_edits')
        first['production_link'] = first['photographer_link'] = first['folder_url']
        second = dict(first, id=8)
        access = gallery_access([first, second])
        self.assertEqual(len(access), 6)
        self.assertEqual({v['group_id'] for v in access.values()}, {7, 8})
