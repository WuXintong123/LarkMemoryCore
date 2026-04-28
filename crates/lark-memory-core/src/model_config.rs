use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ModelsFile {
    pub models: Vec<ModelRecord>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ModelRecord {
    pub id: String,
    #[serde(default)]
    pub owned_by: String,
    #[serde(default)]
    pub created: i64,
    pub serving: ServingPolicy,
    pub tool: ToolConfig,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ServingPolicy {
    pub api_mode: String,
    pub prompt_style: String,
    pub default_max_tokens: i32,
    pub max_max_tokens: i32,
    pub max_input_chars: i32,
    pub request_timeout_ms: i32,
    pub stream_idle_timeout_s: i32,
    pub allow_anonymous_models: bool,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ToolConfig {
    pub cli_path: String,
    #[serde(default)]
    pub numactl_nodes: String,
    #[serde(default)]
    pub taskset_cpus: String,
    #[serde(default)]
    pub extra_args: String,
}

#[derive(Debug, Clone)]
pub struct ModelRegistry {
    models: BTreeMap<String, ModelRecord>,
}

impl ModelRegistry {
    pub fn from_file(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("reading model config {}", path.display()))?;
        let parsed: ModelsFile = serde_json::from_str(&raw)
            .with_context(|| format!("parsing model config {}", path.display()))?;
        let models = parsed
            .models
            .into_iter()
            .filter(|model| !model.id.trim().is_empty() && !model.tool.cli_path.trim().is_empty())
            .map(|model| (model.id.clone(), model))
            .collect();
        Ok(Self { models })
    }

    pub fn get(&self, model_id: &str) -> Option<&ModelRecord> {
        self.models.get(model_id)
    }

    pub fn list(&self) -> impl Iterator<Item = &ModelRecord> {
        self.models.values()
    }
}
