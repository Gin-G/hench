{{/* Standard labels applied to every object */}}
{{- define "hench.labels" -}}
app: {{ .Values.app.name }}
group: {{ .Values.app.group }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* CNPG cluster name + the secret it produces for the app role */}}
{{- define "hench.cnpgClusterName" -}}
{{ .Values.app.name }}-cnpg
{{- end -}}

{{- define "hench.cnpgAppSecret" -}}
{{ include "hench.cnpgClusterName" . }}-app
{{- end -}}

{{- define "hench.configSecret" -}}
{{ .Values.app.name }}-config
{{- end -}}

{{- define "hench.longmontSecret" -}}
{{ .Values.app.name }}-longmont
{{- end -}}

{{- define "hench.basicAuthSecret" -}}
{{ .Values.app.name }}-basicauth
{{- end -}}

{{/*
Backend container env, shared by the API Deployment, its alembic init
container, and the sync CronJob. DATABASE_URL comes from the CNPG app secret;
Plaid + Fernet secrets come from the ESO-managed config secret.
*/}}
{{- define "hench.backendEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.cnpgAppSecret" . }}
      key: uri
- name: PLAID_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.configSecret" . }}
      key: PLAID_CLIENT_ID
- name: PLAID_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.configSecret" . }}
      key: PLAID_SECRET
- name: FERNET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.configSecret" . }}
      key: FERNET_KEY
- name: PLAID_ENV
  value: {{ .Values.backend.env.PLAID_ENV | quote }}
- name: PLAID_PRODUCTS
  value: {{ .Values.backend.env.PLAID_PRODUCTS | quote }}
{{- with .Values.backend.env.PLAID_ADDITIONAL_CONSENTED_PRODUCTS }}
- name: PLAID_ADDITIONAL_CONSENTED_PRODUCTS
  value: {{ . | quote }}
{{- end }}
- name: PLAID_COUNTRY_CODES
  value: {{ .Values.backend.env.PLAID_COUNTRY_CODES | quote }}
- name: PLAID_WEBHOOK_URL
  value: {{ printf "https://%s/api/webhook" .Values.frontend.fqdn | quote }}
- name: CORS_ORIGINS
  value: {{ printf "[\"https://%s\"]" .Values.frontend.fqdn | quote }}
- name: CF_ACCESS_TEAM_DOMAIN
  value: {{ required "cfAccess.teamDomain is required" .Values.cfAccess.teamDomain | quote }}
- name: CF_ACCESS_AUD
  value: {{ required "cfAccess.aud is required: copy the AUD tag from the hench Access application" .Values.cfAccess.aud | quote }}
- name: CF_ACCESS_ALLOWED_EMAILS
  value: {{ required "cfAccess.allowedEmails is required" .Values.cfAccess.allowedEmails | toJson | quote }}
{{- if .Values.longmont.enabled }}
# Optional so the pods still start if the Longmont secret has not synced; the
# utility fetch then reports itself unconfigured instead.
- name: LONGMONT_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.longmontSecret" . }}
      key: LONGMONT_USERNAME
      optional: true
- name: LONGMONT_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "hench.longmontSecret" . }}
      key: LONGMONT_PASSWORD
      optional: true
{{- end }}
- name: LOG_LEVEL
  value: {{ .Values.backend.env.LOG_LEVEL | quote }}
- name: HOST
  value: "0.0.0.0"
- name: PORT
  value: {{ .Values.backend.port | quote }}
{{- end -}}
