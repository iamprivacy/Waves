"""A TIDAL playlist folder's name follows the stand-ins like every other name.

{folder_path} is resolved before format_path_media (its separators have to
survive, and the formatter deletes them), so it was the one library-bound name
that never saw the illegal-character stand-ins. A folder called "?" therefore
lost its level entirely and a folder called "Chill: Night" ignored the " · "
the user chose for ":", while an album with the same name kept both (issue #16).
"""

from tidaler.helper.folders import apply_folder_path, sanitize_folder_path

TEMPLATE = "Playlists/{folder_path}{playlist_name}"


class TestFolderNamesUseTheStandIns:
    def test_a_folder_named_only_of_rejected_characters_keeps_a_level(self):
        assert sanitize_folder_path("?", illegal_map={"?": "？"}) == "？"

    def test_a_rejected_character_inside_a_folder_name_uses_its_stand_in(self):
        assert sanitize_folder_path("Chill: Night", illegal_map={":": " · "}) == "Chill · Night"

    def test_the_general_stand_in_applies_to_the_rest(self):
        assert sanitize_folder_path("AC/DC picks", illegal_replacement="-") == "AC/DC picks"
        assert sanitize_folder_path("Rock*Pop", illegal_replacement="-") == "Rock-Pop"

    def test_separators_still_split_into_levels(self):
        assert sanitize_folder_path("Country/Bluegrass", illegal_map={":": " · "}) == "Country/Bluegrass"

    def test_without_stand_ins_nothing_changes(self):
        assert sanitize_folder_path("Coun:try?") == "Country"

    def test_a_level_that_empties_out_is_still_dropped(self):
        # No stand-in configured, so "?" leaves nothing and the level goes,
        # exactly as before. The stand-in is what saves it, not this function.
        assert sanitize_folder_path("Rock/?/Live") == "Rock/Live"

    def test_a_folder_named_like_a_template_token_is_still_defused(self):
        assert sanitize_folder_path("Best of {artist_name}", illegal_map={":": " · "}) == "Best of (artist_name)"


class TestApplyFolderPathCarriesTheStandIns:
    def test_the_stand_ins_reach_the_template(self):
        out = apply_folder_path(TEMPLATE, "Chill: Night", illegal_map={":": " · "})

        assert out == "Playlists/Chill · Night/{playlist_name}"

    def test_no_stand_ins_keeps_the_old_answer(self):
        assert apply_folder_path(TEMPLATE, "Chill: Night") == "Playlists/Chill Night/{playlist_name}"

    def test_an_empty_folder_path_is_unaffected(self):
        assert apply_folder_path(TEMPLATE, "", illegal_map={":": " · "}) == "Playlists/{playlist_name}"
