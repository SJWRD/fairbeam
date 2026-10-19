import { t } from "../i18n/index.ts";
import { errorText } from "./errorText.ts";

// Command errors have stable codes. English diagnostics remain in the native response;
// known user-facing failures use complete translated sentences, not a generic 409 prefix.
const KEYS: Record<string, string> = {
  "document_invalid_request": "native.error.document_invalid_request",
  "document_invalid_name": "native.error.document_invalid_name",
  "document_invalid_path": "native.error.document_invalid_path",
  "document_folder_unavailable": "native.error.document_folder_unavailable",
  "document_regular_file": "native.error.document_regular_file",
  "document_json_select": "native.error.document_json_select",
  "document_json_unavailable": "native.error.document_json_unavailable",
  "document_json_size": "native.error.document_json_size",
  "document_json_encoding": "native.error.document_json_encoding",
  "document_selection_expired": "native.error.document_selection_expired",
  "document_other_session": "native.error.document_other_session",
  "document_busy": "native.error.document_busy",
  "document_invalid_response": "native.error.document_invalid_response",
  "document_server_unavailable": "native.error.document_server_unavailable",
  "document_request_failed": "native.error.document_request_failed",
  "document_window_unavailable": "native.error.document_window_unavailable",
  "document_dialog_failed": "native.error.document_dialog_failed",
  "document_import_failed": "native.error.document_import_failed",
  "document_archive_failed": "native.error.document_archive_failed",
  "document_request_size": "native.error.document_request_size",
  "document_connection_failed": "native.error.document_connection_failed",
  "document_request_timeout": "native.error.document_request_timeout",
  "document_response_size": "native.error.document_response_size",
  "document_response_interrupted": "native.error.document_response_interrupted",
  "document_target_exists": "native.error.document_target_exists",
  "export_invalid_name": "native.error.export_invalid_name",
  "export_too_large": "native.error.export_too_large",
  "export_invalid_payload": "native.error.export_invalid_payload",
  "export_extension_unsupported": "native.error.export_extension_unsupported",
  "export_reserved_name": "native.error.export_reserved_name",
  "export_basename": "native.error.export_basename",
  "export_extension_mismatch": "native.error.export_extension_mismatch",
  "export_parent_unavailable": "native.error.export_parent_unavailable",
  "export_regular_file": "native.error.export_regular_file",
  "export_window_unavailable": "native.error.export_window_unavailable",
  "export_dialog_failed": "native.error.export_dialog_failed",
  "export_write_failed": "native.error.export_write_failed",
};
export function nativeErrorMessage(error: unknown, document = false): string {
  const value = error && typeof error === "object" ? error as { code?: unknown; status?: unknown; error?: unknown } : undefined;
  const key = typeof value?.code === "string" ? KEYS[value.code] : undefined;
  if (key) return t(key);
  const message = typeof value?.error === "string" ? value.error : errorText(error);
  return document && value?.status === 409 ? t("document.conflict", { error: message }) : message;
}
