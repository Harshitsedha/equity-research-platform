import { TerminalError } from "../../_components/TerminalError";

export default function StockNotFound() {
  return (
    <TerminalError
      code="404 · STOCK NOT FOUND"
      title="No stock under that ISIN"
      detail="The ISIN is not on coverage. Check the identifier and try again."
    />
  );
}
