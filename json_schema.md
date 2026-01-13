# Email Intelligence JSON Schema

## Overview
This document defines the JSON structures produced by each enrichment phase. All schemas include confidence scores and reasoning chains for explainability.

---

## 1. Raw Message (Pre-Enrichment)

```json
{
  "msg_id": "outlook-unique-id-abc123",
  "conversation_id": "conversation_topic_hash",
  "conversation_topic": "Acme MVP Planning",
  "subject": "RE: Acme MVP - Q4 Scope Discussion",
  "sender_email": "alice@company.com",
  "sender_name": "Alice Chen",
  "recipients": ["bob@company.com", "carol@company.com"],
  "cc": ["david@company.com"],
  "delivery_date": "2025-10-15T10:30:00Z",
  "message_class": "IPM.Note",
  "body_snippet": "Based on the meeting, here's the updated scope for Acme MVP. We'll need to prioritize database schema changes over UI refinements for Q4...",
  "has_ics_attachment": false,
  "message_index": 3,
  "conversation_message_count": 12
}
```

---

## 2. Enriched Message (Post-Ollama)

Wraps raw message with all four extraction tasks.

```json
{
  "msg_id": "outlook-unique-id-abc123",
  "conversation_id": "conversation_topic_hash",
  "conversation_topic": "Acme MVP Planning",
  
  "enrichment": {
    "timestamp_processed": "2025-01-13T14:23:45Z",
    "model_used": "mistral:7b",
    "batch_id": "batch_001_5msgs",
    
    "task_a_projects": {
      "extractions": [
        {
          "extraction": "Acme MVP",
          "type": "project",
          "confidence": 0.92,
          "signal_strength": "high",
          "reasoning": [
            "Explicit mention: 'Acme MVP' appears 2 times in subject and body",
            "Context: Subject line identifies as project discussion",
            "Action words: 'scope discussion', 'prioritize', 'Q4' indicate active execution phase",
            "Stakeholders: Named PM (Alice) and engineers (Bob, Carol) present"
          ],
          "flags": []
        },
        {
          "extraction": "Database Schema Modernization",
          "type": "project_inferred",
          "confidence": 0.65,
          "signal_strength": "medium",
          "reasoning": [
            "Inferred from: 'database schema changes' mentioned as Q4 priority",
            "Could be: Sub-task of Acme MVP OR standalone infrastructure project",
            "Temporal: Mentioned in context of Q4 planning"
          ],
          "flags": [
            "AMBIGUOUS: Could be subcomponent of Acme MVP, not separate project"
          ]
        }
      ],
      "most_likely_project": "Acme MVP",
      "project_confidence": 0.92
    },
    
    "task_b_stakeholders": {
      "extractions": [
        {
          "stakeholder": "Alice Chen",
          "email": "alice@company.com",
          "inferred_role": "Product Manager",
          "role_confidence": 0.95,
          "interaction_type": "decision_maker",
          "evidence": [
            "Sender of email; initiates scope discussion",
            "Uses PM-specific language: 'scope', 'prioritize', 'Q4 roadmap'",
            "Coordination patterns: CC'd others for feedback"
          ],
          "associated_projects": ["Acme MVP"]
        },
        {
          "stakeholder": "Bob Chen",
          "email": "bob@company.com",
          "inferred_role": "Software Engineer",
          "role_confidence": 0.87,
          "interaction_type": "executor",
          "evidence": [
            "Recipient of technical scope discussion",
            "Pattern: Typically responds with implementation questions",
            "Mentioned in context: 'database schema changes' (technical task)"
          ],
          "associated_projects": ["Acme MVP"]
        }
      ],
      "primary_stakeholder": "Alice Chen",
      "stakeholder_count": 3
    },
    
    "task_c_importance": {
      "importance_tier": "execution",
      "tier_confidence": 0.88,
      "weight_multiplier": 2.0,
      "reasoning": [
        "Active discussion of Q4 deliverables and priorities",
        "Meeting follow-up indicates decision-making in progress",
        "Specific technical scope (database changes) = concrete execution work",
        "Not critical-path (no emergency language), not routine overhead"
      ],
      "flags": []
    },
    
    "task_d_meetings": {
      "is_meeting_related": true,
      "meeting_confidence": 0.78,
      "inferred_from": "follow-up_to_meeting",
      "reasoning": [
        "References 'Based on the meeting' in first sentence",
        "Discusses meeting outcomes and decisions",
        "Email is action item delivery, not scheduling"
      ],
      "inferred_meeting_date": "2025-10-14T00:00:00Z",
      "inferred_attendees": ["alice@company.com", "bob@company.com", "carol@company.com"],
      "is_calendar_invite": false,
      "ics_attachment_detected": false,
      "flags": [
        "NO_EXACT_MEETING_DATE: Inferred from 'based on the meeting' language"
      ]
    }
  }
}
```

---

## 3. Aggregated Conversation (Post-Clustering)

Groups enriched messages by conversation, merges project/stakeholder extractions, applies clustering logic.

```json
{
  "conversation_id": "conversation_topic_hash",
  "conversation_topic": "Acme MVP Planning",
  "message_count": 12,
  "date_range": {
    "start": "2025-10-08T09:15:00Z",
    "end": "2025-10-22T16:45:00Z",
    "active_days": 14
  },
  
  "projects": [
    {
      "canonical_name": "Acme MVP",
      "confidence": 0.89,
      "confidence_distribution": {
        "high": 8,
        "medium": 3,
        "low": 1
      },
      "aggregated_reasoning": [
        "Explicit mentions in 8 messages",
        "3 inferred mentions from context",
        "1 ambiguous reference in passing",
        "Consistency: Same project name used across all references",
        "Temporal: Focused engagement Oct 8-22"
      ],
      "message_ids": [
        "msg_001",
        "msg_003",
        "msg_005",
        "msg_007",
        "msg_009",
        "msg_011"
      ],
      "recommendation": "INCLUDE - Strong signal, clear project definition"
    },
    {
      "canonical_name": "UNCERTAIN: Database Infrastructure Work",
      "confidence": 0.48,
      "confidence_distribution": {
        "high": 0,
        "medium": 2,
        "low": 4
      },
      "aggregated_reasoning": [
        "Mentioned as sub-task of Acme MVP in 2 messages",
        "Unclear if standalone project or component",
        "Pattern: Database work mentioned only in scope discussions, not as independent project"
      ],
      "message_ids": ["msg_003", "msg_007"],
      "recommendation": "FLAG_REVIEW - May be subcomponent of Acme MVP, not distinct project"
    }
  ],
  
  "stakeholders": [
    {
      "canonical_name": "Alice Chen",
      "email": "alice@company.com",
      "primary_role": "Product Manager",
      "role_confidence": 0.94,
      "interaction_patterns": {
        "message_count": 5,
        "initiator": true,
        "decision_maker": true,
        "responder": false
      },
      "associated_projects": ["Acme MVP"],
      "influence_score": 0.92,
      "interaction_type": "leadership"
    },
    {
      "canonical_name": "Bob Chen",
      "email": "bob@company.com",
      "primary_role": "Software Engineer",
      "role_confidence": 0.85,
      "interaction_patterns": {
        "message_count": 4,
        "initiator": false,
        "decision_maker": false,
        "responder": true
      },
      "associated_projects": ["Acme MVP"],
      "influence_score": 0.65,
      "interaction_type": "execution"
    }
  ],
  
  "importance_summary": {
    "aggregate_tier": "execution",
    "tier_confidence": 0.86,
    "tier_distribution": {
      "critical": 1,
      "execution": 7,
      "overhead": 3,
      "fyi": 1,
      "noise": 0
    },
    "importance_weighted_count": 13,
    "raw_message_count": 12,
    "weight_multiplier": 1.08
  },
  
  "meeting_summary": {
    "calendar_invites": 2,
    "inferred_meetings": 1,
    "confirmed_meeting_count": 2,
    "meeting_confidence_avg": 0.81
  },
  
  "temporal_pattern": {
    "engagement_type": "focused_burst",
    "active_window": "Oct 8-22 (14 days out of 91-day quarter = 15% of Q4)",
    "distribution": "front_loaded - 60% of messages in first 10 days",
    "interpretation": "Intensive execution phase early in quarter, then maintenance mode"
  },
  
  "enrichment_metadata": {
    "messages_processed": 12,
    "messages_enriched": 11,
    "enrichment_errors": 1,
    "error_fallback_count": 1,
    "last_updated": "2025-01-13T14:45:30Z"
  }
}
```

---

## 4. Canonical Project (Post-Aggregation & Deduplication)

Final canonical representation of a project, merged across conversations.

```json
{
  "project_id": "proj_acme_mvp_001",
  "canonical_name": "Acme MVP",
  "aliases": [
    "Acme MVP Planning",
    "Acme Q4 MVP",
    "ACME MVP Scope"
  ],
  "confidence": 0.89,
  "overall_confidence_reasoning": [
    "Consistent naming across 5 separate conversations",
    "Named meeting artifacts and calendar invites reference this project",
    "Multiple high-confidence extractions (≥0.85) from independent sources",
    "No contradictory information or naming conflicts"
  ],
  
  "engagement_summary": {
    "total_conversations": 5,
    "total_messages": 47,
    "importance_weighted_messages": 94,
    "meeting_invites": 3,
    "estimated_meeting_hours": 3.5,
    "date_range": {
      "start": "2025-10-08T09:15:00Z",
      "end": "2025-12-15T17:00:00Z"
    },
    "active_days": 68,
    "engagement_ratio": 0.75
  },
  
  "stakeholders": [
    {
      "name": "Alice Chen",
      "email": "alice@company.com",
      "role": "Product Manager",
      "involvement_level": "leadership",
      "message_count": 15,
      "interaction_frequency": "high"
    },
    {
      "name": "Bob Chen",
      "email": "bob@company.com",
      "role": "Software Engineer",
      "involvement_level": "execution",
      "message_count": 12,
      "interaction_frequency": "medium"
    },
    {
      "name": "Carol Wong",
      "email": "carol@company.com",
      "role": "QA Engineer",
      "involvement_level": "execution",
      "message_count": 8,
      "interaction_frequency": "medium"
    }
  ],
  
  "phases": [
    {
      "phase_name": "Scope & Planning",
      "date_range": {
        "start": "2025-10-08T00:00:00Z",
        "end": "2025-10-22T00:00:00Z"
      },
      "duration_days": 14,
      "message_count": 18,
      "key_activity": "Defining Q4 deliverables, technical approach"
    },
    {
      "phase_name": "Execution",
      "date_range": {
        "start": "2025-10-23T00:00:00Z",
        "end": "2025-12-10T00:00:00Z"
      },
      "duration_days": 48,
      "message_count": 22,
      "key_activity": "Implementation, reviews, progress updates"
    },
    {
      "phase_name": "Closure",
      "date_range": {
        "start": "2025-12-11T00:00:00Z",
        "end": "2025-12-15T00:00:00Z"
      },
      "duration_days": 4,
      "message_count": 7,
      "key_activity": "Final sign-off, handoff"
    }
  ],
  
  "recommendation": "INCLUDE - High confidence, significant engagement, clear strategic importance"
}
```

---

## 5. Final Report Output (Markdown-Ready)

Structured for easy rendering into human-readable format.

```json
{
  "report_metadata": {
    "title": "Q4 2025 Quarterly Engagement Summary",
    "period": "2025-10-01 to 2025-12-31",
    "generated_at": "2025-01-13T15:00:00Z",
    "total_messages_analyzed": 347,
    "total_conversations_analyzed": 43,
    "enrichment_quality": {
      "avg_confidence": 0.81,
      "high_confidence_count": 28,
      "medium_confidence_count": 10,
      "low_confidence_count": 5
    }
  },
  
  "sections": {
    "confident_projects": [
      {
        "rank": 1,
        "name": "Acme MVP",
        "confidence": 0.89,
        "messages": 47,
        "meetings": 3,
        "key_stakeholders": ["Alice Chen (PM)", "Bob Chen (Engineer)"],
        "timeline": "Oct 8 - Dec 15 (68 active days)",
        "pattern": "Front-loaded planning, steady execution, tapering closure",
        "narrative": "Intensive Q4 execution on core MVP deliverables with focused team engagement."
      },
      {
        "rank": 2,
        "name": "Fortune 50 Demo Lab",
        "confidence": 0.91,
        "messages": 38,
        "meetings": 4,
        "key_stakeholders": ["David Lee (Director)", "Alice Chen (PM)"],
        "timeline": "Oct 1 - Dec 31 (continuous)",
        "pattern": "Consistent engagement throughout quarter",
        "narrative": "Ongoing partnership setup for innovation lab access, recurring stakeholder sync."
      }
    ],
    
    "review_required_projects": [
      {
        "rank": 3,
        "name": "Quarterly Resource Planning",
        "confidence": 0.68,
        "messages": 5,
        "meetings": 1,
        "key_stakeholders": ["Finance Team"],
        "timeline": "Nov 1-15 (15 days)",
        "uncertainty": "Could be routine planning or strategic initiative; unclear scope",
        "note": "Manual review recommended to determine if this should be listed as project."
      }
    ],
    
    "uncertain_threads": [
      {
        "topic": "Random Technical Discussions",
        "count": 6,
        "confidence_avg": 0.35,
        "assessment": "No clear project signal; likely knowledge-sharing or ad-hoc questions"
      }
    ]
  },
  
  "stakeholder_graph": {
    "top_collaborators": [
      {
        "name": "Alice Chen",
        "role": "Product Manager",
        "projects": ["Acme MVP", "Fortune 50 Demo Lab"],
        "influence": "high",
        "collaboration_style": "initiator, decision-maker"
      },
      {
        "name": "Bob Chen",
        "role": "Software Engineer",
        "projects": ["Acme MVP"],
        "influence": "medium",
        "collaboration_style": "executor, responder"
      }
    ]
  },
  
  "insights": {
    "engagement_pattern": "You had 2 major projects dominating Q4, with heavy front-loaded planning and consistent execution.",
    "temporal_observation": "70% of your engagement happened in the first 8 weeks; December was lighter.",
    "stakeholder_observation": "Core team of 3-4 people drove most work; limited cross-team collaboration.",
    "recommendation": "Projects align well with business priorities; consider documenting lessons learned from Acme MVP execution phase."
  }
}
```

---

## 6. Configuration Schema

```json
{
  "config.json": {
    "ollama": {
      "url": "http://localhost:11434",
      "model": "mistral:7b",
      "timeout_seconds": 30,
      "max_retries": 3,
      "retry_backoff_ms": 500
    },
    "processing": {
      "batch_size": 5,
      "max_workers": 2,
      "log_level": "INFO"
    },
    "thresholds": {
      "confidence_high": 0.80,
      "confidence_medium": 0.50,
      "confidence_low": 0.00
    },
    "clustering": {
      "embedding_similarity_threshold": 0.75,
      "enable_deduplication": true
    },
    "output": {
      "generate_json": true,
      "generate_markdown": true,
      "generate_csv": true,
      "output_dir": "./data"
    }
  }
}
```

---

## Notes

- All timestamps are ISO 8601 format (UTC)
- Confidence scores are 0.0–1.0 (float)
- Message/conversation IDs must be globally unique; use hash of Outlook unique ID
- Reasoning chains are always arrays of strings for consistency
- Flags array contains error/warning codes for programmatic filtering
- Temporal patterns support later analysis/dashboard building
