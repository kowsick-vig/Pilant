// Ported from studio.py's _CONNECTOR_ACCENT / _DEFAULT_CONNECTOR_ACCENT —
// keeps each connector's colored avatar/icon chip consistent between the
// sidebar's workflow cards and the Integrations grid, same as the original.
export const CONNECTOR_ACCENT = {
  gmail: { bg: "#FCE9E7", fg: "#C7462F" },
  slack: { bg: "#F1E7FB", fg: "#6B3FA0" },
  github: { bg: "#EAEBEF", fg: "#20242E" },
  helpdesk: { bg: "#E5F0FC", fg: "#1A5FB4" },
  jira: { bg: "#E7ECFC", fg: "#2E52B8" },
  unified: { bg: "#EEEAFE", fg: "#6C7CFF" },
  custom: { bg: "#FBEFDE", fg: "#B5691D" },
  knowledge_base: { bg: "#E7F5EF", fg: "#227A55" },
};
export const DEFAULT_CONNECTOR_ACCENT = { bg: "#F0F2F7", fg: "#6B7385" };

export function connectorAccent(key) {
  return CONNECTOR_ACCENT[key] || DEFAULT_CONNECTOR_ACCENT;
}
