"""The vocabulary refuses what it does not define, and never widens a grant by itself."""

import pytest

from tg_agentd import verbs


def test_a_narrow_action_is_not_a_consequence_of_a_broad_one():
    # Each pair is a separate grant on purpose. The left word must never carry the right
    for broad, narrow in [
        ("read", "media"),
        ("send", "forward"),
        ("send", "mark-read"),
        ("delete", "delete-for-all"),
    ]:
        assert verbs.granted(narrow, {broad}, verbs.CHAT_ACTIONS) is None


def test_an_action_is_granted_by_its_own_name():
    assert verbs.granted("send", {"read", "send"}, verbs.CHAT_ACTIONS) == "send"


def test_the_wildcard_grants_an_action_and_reports_itself():
    # The caller reports the word that permitted the action, so a wildcard is visible
    assert verbs.granted("delete-for-all", {"all"}, verbs.CHAT_ACTIONS) == "all"


def test_the_wildcard_grants_nothing_the_table_lacks():
    assert verbs.granted("launch-missiles", {"all"}, verbs.CHAT_ACTIONS) is None


def test_an_undefined_word_is_reported_rather_than_ignored():
    assert verbs.unknown_words(["read", "shout"], verbs.CHAT_ACTIONS) == ["shout"]
    assert verbs.unknown_words(["all", "read"], verbs.CHAT_ACTIONS) == []


def test_the_wildcard_expands_to_the_whole_table():
    assert verbs.expand("all", verbs.CHAT_ACTIONS) == sorted(verbs.CHAT_ACTIONS)
    assert verbs.expand("read", verbs.CHAT_ACTIONS) == ["read"]


def test_folder_actions_that_widen_reach_are_named_as_such():
    for action in ["folder-edit", "folder-create", "folder-delete"]:
        assert verbs.widens_reach(action)
    for action in ["folder-read", "folder-order", "folder-rename", "read", "digest"]:
        assert not verbs.widens_reach(action)


def test_a_folder_granting_content_is_recognised_whatever_word_did_it():
    assert verbs.grants_content({"read"})
    assert verbs.grants_content({"digest"})
    assert verbs.grants_content({"media"})
    # A wildcard on a folder grants reading too, so it makes that folder uneditable
    assert verbs.grants_content({"all"})
    assert not verbs.grants_content({"folder-read", "folder-rename"})


@pytest.mark.parametrize("table", [verbs.CHAT_ACTIONS, verbs.FOLDER_ACTIONS])
def test_every_action_carries_a_description_for_the_help(table):
    for action, description in table.items():
        assert description.strip(), action
