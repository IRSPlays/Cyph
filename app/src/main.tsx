import "@fontsource/pixelify-sans/400.css";
import "@fontsource/pixelify-sans/600.css";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";
import "./styles/base.css";
import "./styles/corner.css";
import React from "react";
import ReactDOM from "react-dom/client";
import "./crash";
import { showFatal } from "./crash";
import App from "./ui/App";
import { WidgetApp } from "./ui/Widget";

/** The companion window is the same page with a different root: Tauri names it "widget"; `#/widget` previews it in a browser. */
const isWidget =
  (window as unknown as { __TAURI_INTERNALS__?: { metadata?: { currentWindow?: { label?: string } } } }).__TAURI_INTERNALS__?.metadata?.currentWindow?.label === "widget" ||
  location.hash.startsWith("#/widget");

class Boundary extends React.Component<{ children: React.ReactNode }, { bad: boolean }> {
  state = { bad: false };
  static getDerivedStateFromError() {
    return { bad: true };
  }
  componentDidCatch(e: unknown) {
    showFatal(e);
  }
  render() {
    return this.state.bad ? null : this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Boundary>
      {isWidget ? <WidgetApp /> : <App />}
    </Boundary>
  </React.StrictMode>,
);
