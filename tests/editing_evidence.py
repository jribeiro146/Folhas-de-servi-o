"""Build valid synthetic commit evidence through the editing service API."""
from src.services.editing_state_service import EditingStateService, EditorIdentity


def committed_editing_repository(root):
    folder = root / "synthetic-editing-document"
    folder.mkdir()
    path = folder / "synthetic-editing-document.xlsx"
    path.write_bytes(b"synthetic fingerprint fixture")
    service = EditingStateService(root / "editing")
    service.associate_path(path, "document", original_name="synthetic")
    identity = EditorIdentity("synthetic-user", "Synthetic")
    lease = service.acquire_lease(path, identity, "synthetic-tab")
    service.claim_operation(document_id="document", kind="send", idempotency_key="finalize",
                            identity=identity, client_id="synthetic-tab", lease_token=lease["lease"]["token"],
                            base_revision=lease["revision"])
    service.commit_operation(document_id="document", idempotency_key="finalize", path=None,
                             response={"success": True})
    return service.repository
