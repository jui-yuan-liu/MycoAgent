import pytest

from mycoagent.node.workspace import WorkspaceEscape, assignment_workspace, strip_local_paths


def test_assignment_workspace_deletes_on_exit():
    with assignment_workspace() as workspace:
        path = workspace.root
        workspace.write_file("note.txt", "hi")
        assert (path / "note.txt").is_file()
    assert not path.exists()


def test_workspace_file_tools_cannot_escape(tmp_path):
    with assignment_workspace() as workspace:
        with pytest.raises(WorkspaceEscape):
            workspace.resolve_path("../outside.txt")
        with pytest.raises(WorkspaceEscape):
            workspace.resolve_path(str(tmp_path / "nope.txt"))
        workspace.write_file("ok.txt", "in")
        assert workspace.read_file("ok.txt") == "in"
        assert "ok.txt" in workspace.list_dir()


def test_strip_local_paths_hides_workspace():
    with assignment_workspace() as workspace:
        text = f"wrote {workspace.root}/out.txt"
        cleaned = strip_local_paths(text, workspace.root)
        assert str(workspace.root) not in cleaned
        assert "[workspace]" in cleaned


def test_shell_stays_in_workspace_cwd():
    with assignment_workspace() as workspace:
        workspace.write_file("a.txt", "x")
        listed = workspace.run_shell("ls")
        assert "a.txt" in listed
        pwd = workspace.run_shell("pwd")
        assert str(workspace.root) not in pwd
        assert str(workspace.root.resolve()) not in pwd
