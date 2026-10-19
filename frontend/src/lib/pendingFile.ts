/** Bridge for "open tool result in the editor": tool flows park a File
 *  here and navigate to /editor, where EditorPage picks it up once. */
let pending: File | null = null;

export function setPendingEditorFile(file: File): void {
  pending = file;
}

export function takePendingEditorFile(): File | null {
  const file = pending;
  pending = null;
  return file;
}
