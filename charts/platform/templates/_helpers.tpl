{{- define "urumi.name" -}}{{ .Release.Name }}{{- end -}}
{{- define "urumi.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: urumi-mcp-gateway
{{- end -}}
