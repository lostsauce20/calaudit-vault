if (typeof navigator !== 'undefined' && navigator.modelContext) {
  navigator.modelContext.provideContext({
    tools: [
      {
        name: "query_forensic_metadata",
        description: "Access structured JSON-LD metadata regarding Knox-Keene violations and DHCS filings.",
        inputSchema: {
          type: "object",
          properties: {
            target: { type: "string", description: "The specific entity or case to query (e.g., 'LACare', '26AVSC00192')" }
          },
          required: ["target"]
        },
        execute: async (args) => {
          return {
            content: [{ type: "text", text: "Please traverse the /metadata/ directory to access the finalized JSON-LD records for your target." }]
          };
        }
      }
    ]
  });
}