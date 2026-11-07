import { href, type Route } from "../../lib/router";
import { ImportDetail, ImportHistory } from "./ImportHistory";
import { ImportPreview } from "./ImportPreview";

export function ImportsPage({ route }: { route: Route }) {
  const sub = route.segments[1];
  const isPreview = sub === "preview";
  return (
    <div className="stack">
      <h1>Imports</h1>
      <nav aria-label="Import sections" className="tabs">
        <a href={href(["imports"])} aria-current={!sub ? "page" : undefined}>
          Import history
        </a>
        <a href={href(["imports", "preview"])} aria-current={isPreview ? "page" : undefined}>
          Preview a file
        </a>
      </nav>
      {isPreview ? (
        <ImportPreview />
      ) : sub ? (
        <ImportDetail importId={sub} />
      ) : (
        <ImportHistory route={route} />
      )}
    </div>
  );
}
