import { TerminalError } from "../../../../_components/TerminalError";

export default function SnapshotNotFound() {
  return (
    <TerminalError
      code="404 · SNAPSHOT NOT FOUND"
      title="No such snapshot for this stock"
      detail="The snapshot does not exist, or it belongs to a different stock — ownership is enforced, not mere existence."
    />
  );
}
