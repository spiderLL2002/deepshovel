export const ARTIFACT_MIME_BY_EXTENSION = {
  csv: "text/csv",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  jpeg: "image/jpeg",
  jpg: "image/jpeg",
  pdf: "application/pdf",
  png: "image/png",
  tif: "image/tiff",
  tiff: "image/tiff",
  tsv: "text/tab-separated-values",
  txt: "text/plain",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
} as const;

export const ARTIFACT_ACCEPT = Object.keys(ARTIFACT_MIME_BY_EXTENSION)
  .map((extension) => `.${extension}`)
  .join(",");

export const ARTIFACT_EXTENSIONS_BY_MIME = Object.entries(ARTIFACT_MIME_BY_EXTENSION)
  .reduce((extensions, [extension, mimeType]) => {
    const known = extensions.get(mimeType) ?? new Set<string>();
    known.add(extension);
    extensions.set(mimeType, known);
    return extensions;
  }, new Map<string, Set<string>>());

export const ACCEPTED_ARTIFACT_MIME_TYPES = new Set(ARTIFACT_EXTENSIONS_BY_MIME.keys());

export function inferArtifactMimeType(fileName: string, declaredType: string) {
  if (declaredType) return declaredType;
  const extension = fileName.split(".").pop()?.toLowerCase();
  return ARTIFACT_MIME_BY_EXTENSION[extension as keyof typeof ARTIFACT_MIME_BY_EXTENSION]
    ?? "application/octet-stream";
}
