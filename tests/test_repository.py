from app.repository import MetadataRepository
from app.schemas import DocumentMetadata


def test_repository_add_and_deactivate(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    repository = MetadataRepository(str(metadata_file))

    document = DocumentMetadata(
        file_name="resume.pdf",
        file_type="pdf",
        doc_category="resume",
        tags=["resume", "latest"],
        storage_path="uploads/resume.pdf",
    )

    repository.add(document)
    assert len(repository.list_active()) == 1

    archived = repository.deactivate(document.id)
    assert archived is True
    assert len(repository.list_active()) == 0
    assert len(repository.list_all()) == 1
