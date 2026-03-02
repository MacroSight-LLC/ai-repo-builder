import React from "react";
import { marked } from "marked";
import { sanitizeHtml } from "./sanitize";
import "./VariablePopup.css";

interface VariablePopupProps {
  variable: {
    name: string;
    type: string;
    description?: string;
    value_preview: string;
    count_items?: number;
  };
  onClose: () => void;
}

const VariablePopup: React.FC<VariablePopupProps> = ({ variable, onClose }) => {
  const handleDownload = () => {
    // Check if variable is a dict type and try to download as JSON
    if (variable.type === "dict") {
      try {
        // Attempt to parse the value_preview as JSON
        const jsonData = JSON.parse(variable.value_preview);
        const content = JSON.stringify(jsonData, null, 2);
        // Use octet-stream to force the browser to respect the .json extension
        const blob = new Blob([content], { type: "application/octet-stream" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${variable.name}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        return;
      } catch (error) {
        // If JSON parsing fails, fall back to markdown
        console.warn(
          "Failed to parse dict as JSON, falling back to markdown download:",
          error,
        );
      }
    }

    // Default to markdown download
    const content = `# Variable: ${variable.name}\n\n**Type:** ${variable.type}\n\n${variable.description ? `**Description:** ${variable.description}\n\n` : ""}**Value:**\n\`\`\`\n${variable.value_preview}\n\`\`\``;
    const blob = new Blob([content], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${variable.name}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const formattedContent = `## ${variable.name}\n\n**Type:** \`${variable.type}\`${variable.count_items ? ` (${variable.count_items} items)` : ""}\n\n${variable.description ? `**Description:** ${variable.description}\n\n` : ""}**Value:**\n\`\`\`\n${variable.value_preview}\n\`\`\``;

  return (
    <div className="variable-popup-overlay" onClick={onClose}>
      <div
        className="variable-popup-content"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="variable-popup-header">
          <h3>Variable Details</h3>
          <div className="variable-popup-actions">
            <button
              className="variable-popup-download-btn"
              onClick={handleDownload}
              title={
                variable.type === "dict"
                  ? "Download as JSON"
                  : "Download as Markdown"
              }
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="currentColor"
              >
                <path d="M8.5 1a.5.5 0 0 0-1 0v8.793L5.354 7.646a.5.5 0 1 0-.708.708l3 3a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 9.793V1z" />
                <path d="M3 13h10a1 1 0 0 0 1-1v-1.5a.5.5 0 0 0-1 0V12H3v-.5a.5.5 0 0 0-1 0V12a1 1 0 0 0 1 1z" />
              </svg>
              Download {variable.type === "dict" ? "JSON" : "MD"}
            </button>
            <button className="variable-popup-close-btn" onClick={onClose}>
              ×
            </button>
          </div>
        </div>
        <div
          className="variable-popup-body"
          dangerouslySetInnerHTML={{
            __html: sanitizeHtml(String(marked(formattedContent))),
          }}
        />
      </div>
    </div>
  );
};

export default VariablePopup;
