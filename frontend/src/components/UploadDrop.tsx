import { useId, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api";
import { useErrorMessage } from "../hooks";

interface UploadDropProps {
  drawerId: number;
  onUploaded: () => void;
}

function isPdf(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

export function UploadDrop({ drawerId, onUploaded }: UploadDropProps) {
  const { t } = useTranslation();
  const errorMessage = useErrorMessage();
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    if (!isPdf(file)) {
      setError(t("upload.notPdf"));
      return;
    }
    setError(null);
    setUploading(file.name);
    try {
      await api.uploadDocument(drawerId, file);
      onUploaded();
    } catch (e) {
      setError(t("upload.failed", { message: errorMessage(e) }));
    } finally {
      setUploading(null);
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void upload(event.dataTransfer.files);
  };

  return (
    <div className="upload">
      <label
        htmlFor={inputId}
        className={`dropzone${dragging ? " is-dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          id={inputId}
          className="file-input"
          type="file"
          accept="application/pdf,.pdf"
          disabled={uploading !== null}
          onChange={(event) => {
            void upload(event.target.files);
            event.target.value = "";
          }}
        />
        {uploading ? (
          <span role="status">{t("upload.uploading", { name: uploading })}</span>
        ) : (
          <span>
            {t("upload.drop")} {t("upload.or")} <span className="link-like">{t("upload.choose")}</span>
          </span>
        )}
      </label>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
