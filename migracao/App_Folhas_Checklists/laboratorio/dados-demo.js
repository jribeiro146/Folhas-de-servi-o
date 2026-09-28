window.MIGRACAO_DEMO = {
  "catalog": {
    "schema_version": 1,
    "serviceTypeOptions": [
      {
        "key": "piquete",
        "label": "Piquete",
        "excel_label": "PIQ"
      },
      {
        "key": "assistencia",
        "label": "Assistência",
        "excel_label": "ASSIST"
      },
      {
        "key": "manutencao",
        "label": "Manutenção",
        "excel_label": "MAN"
      },
      {
        "key": "formacao",
        "label": "Formação",
        "excel_label": "FORM"
      },
      {
        "key": "colocacao_servico",
        "label": "Colocação de serviço",
        "excel_label": "COL.SERV"
      },
      {
        "key": "reparacao_oficina",
        "label": "Reparação oficina",
        "excel_label": "REP.OF"
      },
      {
        "key": "garantia",
        "label": "Garantia",
        "excel_label": "GAR"
      },
      {
        "key": "instalacao",
        "label": "Instalação",
        "excel_label": "INST"
      }
    ],
    "equipmentOptions": [
      {
        "key": "sadi",
        "label": "SADI",
        "excel_label": "SADI"
      },
      {
        "key": "vss",
        "label": "VSS",
        "excel_label": "CCTV"
      },
      {
        "key": "sadco",
        "label": "SADCO",
        "excel_label": "PA/VA"
      },
      {
        "key": "sadir",
        "label": "SADIR",
        "excel_label": "SAI"
      },
      {
        "key": "sadei",
        "label": "SADEI",
        "excel_label": "EXT"
      },
      {
        "key": "sca",
        "label": "SCA",
        "excel_label": "SCA"
      },
      {
        "key": "eas",
        "label": "EAS",
        "excel_label": "EAS"
      },
      {
        "key": "sadg",
        "label": "SADG",
        "excel_label": "SADG"
      },
      {
        "key": "sch",
        "label": "SCH",
        "excel_label": "SCH"
      },
      {
        "key": "other",
        "label": "OTHER",
        "excel_label": "OTHER"
      }
    ],
    "validation": {
      "requiredFields": [
        {
          "key": "customer_name",
          "label": "Cliente / Customer"
        }
      ],
      "technicianRequiredFields": [
        {
          "key": "technician",
          "label": "Nome"
        },
        {
          "key": "start_time",
          "label": "Hora de início"
        },
        {
          "key": "end_time",
          "label": "Hora de fim"
        },
        {
          "key": "total_hours",
          "label": "Total de horas"
        },
        {
          "key": "date",
          "label": "Data"
        }
      ],
      "signatureRequiredFields": [
        {
          "key": "customer_signer_name",
          "label": "Primeiro e último nome"
        },
        {
          "key": "customer_signature_date",
          "label": "Data da assinatura"
        }
      ]
    },
    "limits": {
      "materials": 12,
      "technicians": 4
    },
    "technicianOptions": [
      "Técnico Demonstração A",
      "Técnico Demonstração B"
    ],
    "languages": [
      "pt",
      "en"
    ]
  },
  "definition": {
    "version": "sadi-2",
    "groups": {
      "conventional": "Central convencional",
      "addressable": "Central endereçável",
      "repeater": "Repetidor"
    },
    "fields": {
      "conventional": [
        [
          "brand",
          "Marca",
          "text"
        ],
        [
          "model",
          "Modelo",
          "text"
        ],
        [
          "location",
          "Local da central",
          "text"
        ],
        [
          "total",
          "N.º zonas total",
          "number"
        ],
        [
          "used",
          "N.º zonas em uso",
          "number"
        ],
        [
          "detectors",
          "N.º de detetores",
          "number"
        ],
        [
          "buttons",
          "N.º de botoneiras",
          "number"
        ],
        [
          "sirens",
          "N.º de sirenes",
          "number"
        ]
      ],
      "addressable": [
        [
          "brand",
          "Marca",
          "text"
        ],
        [
          "model",
          "Modelo",
          "text"
        ],
        [
          "location",
          "Local da central",
          "text"
        ],
        [
          "total",
          "N.º loops central",
          "number"
        ],
        [
          "used",
          "N.º loops em uso",
          "number"
        ]
      ],
      "repeater": [
        [
          "brand",
          "Marca",
          "text"
        ],
        [
          "model",
          "Modelo",
          "text"
        ],
        [
          "location",
          "Local do repetidor",
          "text"
        ]
      ]
    },
    "periods": {
      "monthly": "Mensal",
      "quarterly": "Trimestral",
      "half_yearly": "Semestral",
      "annual": "Anual",
      "other": "Outra"
    },
    "general": [
      [
        "B22",
        "Verificação de que não existiram alterações ao projeto e/ou Medidas de Autoproteção."
      ],
      [
        "B23",
        "Verificação dos eventos registados nos registos das Medidas de Autoproteção."
      ]
    ],
    "conventional": [
      [
        "B44",
        "Verificar funcionamento geral incluindo teclado e chaves"
      ],
      [
        "B45",
        "Verificar funcionalidade e nível de luminosidade dos leds de falha e/ou alarme"
      ],
      [
        "B46",
        "Verificar estado da alimentação (230 v / 50 Hz)"
      ],
      [
        "B47",
        "Medição da carga e validade das baterias, verificação da tensão de entrada/saída, limpeza e reaperto de bornes"
      ],
      [
        "B48",
        "Confirmação de ligação entre Central Principal e PC de operacionalização quando exista"
      ],
      [
        "B49",
        "Verificação da acessibilidade e existência de sinalética da(s) central(ais)"
      ]
    ],
    "addressable": [
      [
        "B68",
        "Verificar funcionamento geral incluindo teclado e chaves"
      ],
      [
        "B69",
        "Verificar funcionalidade e nível de luminosidade dos leds de falha e/ou alarme"
      ],
      [
        "B70",
        "Verificar estado da alimentação (230 v / 50 Hz)"
      ],
      [
        "B71",
        "Medição da carga e validade das baterias, verificação da tensão de entrada/saída, limpeza e reaperto de bornes"
      ],
      [
        "B72",
        "Confirmação de ligação entre centrais (quando em rede) e repetidores"
      ],
      [
        "B73",
        "Confirmação de ligação entre Central Principal e PC de operacionalização quando exista"
      ],
      [
        "B74",
        "Verificação da acessibilidade e existência de sinalética da(s) central(ais)"
      ]
    ],
    "repeater": [
      [
        "B90",
        "Verificar funcionamento geral incluindo teclado e chaves"
      ],
      [
        "B91",
        "Verificar funcionalidade e nível de luminosidade dos leds de falha e/ou alarme"
      ],
      [
        "B92",
        "Medição da carga e validade das baterias, verificação da tensão de entrada/saída, limpeza e reaperto de bornes"
      ],
      [
        "B93",
        "Verificação da acessibilidade e existência de sinalética dos repetidores"
      ]
    ],
    "peripherals": [
      [
        "B107",
        "Verificação da alimentação de fontes auxiliares da instalação"
      ],
      [
        "B108",
        "Verificação da carga e validade das baterias e reaperto de bornes das fontes auxiliares"
      ],
      [
        "B109",
        "Verificação do estado geral de conservação dos detetores"
      ],
      [
        "B110",
        "Verificação do estado geral de conservação acessibilidade e sinalização dos botões de Alarme"
      ],
      [
        "B111",
        "Verificação do estado geral de conservação das sirenes"
      ],
      [
        "B112",
        "Verificação do estado geral das cablagens"
      ],
      [
        "B113",
        "Inspeção visual para verificar se ocorreram mudanças estruturais ou ocupacionais que tenham afetado os requisitos para a localização de botões de alarme manual, detetores e sirenes"
      ],
      [
        "B115",
        "A inspeção visual para confirmar que um espaço de pelo menos 0,5 m é conservado desimpedido em todas direções abaixo de cada detetor e que todos os botões de alarme manual permanecem desobstruídos e conspícuos"
      ]
    ],
    "trials": [
      [
        "B121",
        "Ensaios e testes às funções gerais das centrais"
      ],
      [
        "B122",
        "Ensaio e teste de todos os detetores"
      ],
      [
        "B123",
        "Ensaio e teste de todos os botões"
      ],
      [
        "B124",
        "Ensaio e teste de todas as sirenes"
      ],
      [
        "B125",
        "Ensaio funcional dos comandos"
      ],
      [
        "B126",
        "Ensaio da comunicação de alarme ao corpo de bombeiros ou central recetora de alarmes"
      ],
      [
        "B127",
        "Execução de simulação de um alarme por zona e análise das ativações (*)"
      ]
    ],
    "start_warning": "ATENÇÃO - VERIFICAR SE OCUPANTES FORAM AVISADOS DO ENSAIO. Colocar o Sistema em modo Teste e/ou verificar se comandos podem ser ativados.",
    "end_warning": "Repor todos os sistemas em situação normal de funcionamento. Não esquecer de retirar \"modo teste\" e/ou \"modo service\".",
    "trial_warning": "(*) Para esta operação poderão ter de ser desativados alguns módulos de dispositivos de proteção.",
    "coverage_warning": "Quando o procedimento seja mensal, trimestral ou semestral indicar em observações a % de elementos testados ou preferencialmente as áreas do edifício onde os elementos foram testados de forma que ao longo do ano 100% dos dispositivos e todas as zonas sejam testados.",
    "photo_limits": {
      "count": 10,
      "bytes": 1000000,
      "edge": 1600,
      "source_bytes": 10000000
    }
  },
  "cases": [
    {
      "id": "folha-valida",
      "title": "Folha com campos completos",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": true,
          "manutencao": false,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": []
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": []
      },
      "note": "A validação de campos JS não comprova assinatura, arquivo ou finalização."
    },
    {
      "id": "folha-incompleta",
      "title": "Folha por preencher",
      "document": {
        "document_language": "pt",
        "service_number": "",
        "customer_name": "",
        "requested_by": "",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "",
        "work_number": "",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "",
        "intervention_report": "",
        "customer_signer_name": "",
        "customer_signature_date": "",
        "service_types": {
          "piquete": false,
          "assistencia": false,
          "manutencao": false,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": false,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "",
            "start_time": "",
            "end_time": "",
            "total_hours": "",
            "total_hours_overridden": false,
            "date": ""
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": []
      },
      "expected": {
        "documentMissing": 8,
        "documentInvalid": 0,
        "siteErrors": []
      },
      "note": "Rascunhos admitem campos em falta; finalizar exige corrigir."
    },
    {
      "id": "cliente-ausente",
      "title": "Folha com cliente ausente",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "",
        "customer_signature_date": "",
        "service_types": {
          "piquete": false,
          "assistencia": true,
          "manutencao": false,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": true,
        "maintenance_checklists": []
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": []
      },
      "note": "Dispensa detalhes de assinatura da FS; não dispensa assinaturas SADI."
    },
    {
      "id": "sadi-por-assinar",
      "title": "SADI completa nos campos, por assinar",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": false,
          "manutencao": true,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": [
          {
            "id": "site-demo-0001",
            "version": "sadi-2",
            "location": "Local Fictício A",
            "date": "2026-09-28",
            "technician": "Técnico Demonstração A",
            "scie": "",
            "departure": "",
            "period": "annual",
            "period_other": "",
            "observations": "",
            "final_observations": "",
            "peripheral_observations": "",
            "coverage_percent": "",
            "coverage_areas": "",
            "general": {
              "B22": {
                "answer": "OK",
                "justification": ""
              },
              "B23": {
                "answer": "OK",
                "justification": ""
              }
            },
            "peripherals": {
              "B107": {
                "answer": "OK",
                "justification": ""
              },
              "B108": {
                "answer": "OK",
                "justification": ""
              },
              "B109": {
                "answer": "OK",
                "justification": ""
              },
              "B110": {
                "answer": "OK",
                "justification": ""
              },
              "B111": {
                "answer": "OK",
                "justification": ""
              },
              "B112": {
                "answer": "OK",
                "justification": ""
              },
              "B113": {
                "answer": "OK",
                "justification": ""
              },
              "B115": {
                "answer": "OK",
                "justification": ""
              }
            },
            "trials": {
              "B121": {
                "answer": "OK",
                "justification": ""
              },
              "B122": {
                "answer": "OK",
                "justification": ""
              },
              "B123": {
                "answer": "OK",
                "justification": ""
              },
              "B124": {
                "answer": "OK",
                "justification": ""
              },
              "B125": {
                "answer": "OK",
                "justification": ""
              },
              "B126": {
                "answer": "OK",
                "justification": ""
              },
              "B127": {
                "answer": "OK",
                "justification": ""
              }
            },
            "configuration": {
              "conventional": true,
              "addressable": false,
              "repeater": false
            },
            "conventional": [
              {
                "id": "unit-demo-0001",
                "brand": "Marca Fictícia",
                "model": "Modelo Demo",
                "location": "Receção fictícia",
                "total": "4",
                "used": "2",
                "detectors": "12",
                "buttons": "2",
                "sirens": "2",
                "observations": "",
                "checks": {
                  "B44": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B45": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B46": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B47": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B48": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B49": {
                    "answer": "OK",
                    "justification": ""
                  }
                }
              }
            ],
            "addressable": [],
            "repeater": [],
            "photos": [],
            "signatures": {},
            "signature_drafts": {},
            "peripheral_history": []
          }
        ]
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": [
          0
        ],
        "siteStatus": [
          "Por assinar"
        ]
      },
      "note": "Não contém imagens nem tokens de assinatura."
    },
    {
      "id": "sadi-nc-sem-justificacao",
      "title": "SADI com NC sem justificação",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": false,
          "manutencao": true,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": [
          {
            "id": "site-demo-0001",
            "version": "sadi-2",
            "location": "Local Fictício A",
            "date": "2026-09-28",
            "technician": "Técnico Demonstração A",
            "scie": "",
            "departure": "",
            "period": "annual",
            "period_other": "",
            "observations": "",
            "final_observations": "",
            "peripheral_observations": "",
            "coverage_percent": "",
            "coverage_areas": "",
            "general": {
              "B22": {
                "answer": "NC",
                "justification": ""
              },
              "B23": {
                "answer": "OK",
                "justification": ""
              }
            },
            "peripherals": {
              "B107": {
                "answer": "OK",
                "justification": ""
              },
              "B108": {
                "answer": "OK",
                "justification": ""
              },
              "B109": {
                "answer": "OK",
                "justification": ""
              },
              "B110": {
                "answer": "OK",
                "justification": ""
              },
              "B111": {
                "answer": "OK",
                "justification": ""
              },
              "B112": {
                "answer": "OK",
                "justification": ""
              },
              "B113": {
                "answer": "OK",
                "justification": ""
              },
              "B115": {
                "answer": "OK",
                "justification": ""
              }
            },
            "trials": {
              "B121": {
                "answer": "OK",
                "justification": ""
              },
              "B122": {
                "answer": "OK",
                "justification": ""
              },
              "B123": {
                "answer": "OK",
                "justification": ""
              },
              "B124": {
                "answer": "OK",
                "justification": ""
              },
              "B125": {
                "answer": "OK",
                "justification": ""
              },
              "B126": {
                "answer": "OK",
                "justification": ""
              },
              "B127": {
                "answer": "OK",
                "justification": ""
              }
            },
            "configuration": {
              "conventional": true,
              "addressable": false,
              "repeater": false
            },
            "conventional": [
              {
                "id": "unit-demo-0001",
                "brand": "Marca Fictícia",
                "model": "Modelo Demo",
                "location": "Receção fictícia",
                "total": "4",
                "used": "2",
                "detectors": "12",
                "buttons": "2",
                "sirens": "2",
                "observations": "",
                "checks": {
                  "B44": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B45": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B46": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B47": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B48": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B49": {
                    "answer": "OK",
                    "justification": ""
                  }
                }
              }
            ],
            "addressable": [],
            "repeater": [],
            "photos": [],
            "signatures": {},
            "signature_drafts": {},
            "peripheral_history": []
          }
        ]
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": [
          1
        ],
        "siteStatus": [
          "Em preenchimento"
        ]
      },
      "note": "Cada resposta NC precisa da sua justificação; não bloqueia por ser NC quando justificada."
    },
    {
      "id": "sadi-mensal-sem-cobertura",
      "title": "SADI mensal sem cobertura",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": false,
          "manutencao": true,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": [
          {
            "id": "site-demo-0001",
            "version": "sadi-2",
            "location": "Local Fictício A",
            "date": "2026-09-28",
            "technician": "Técnico Demonstração A",
            "scie": "",
            "departure": "",
            "period": "monthly",
            "period_other": "",
            "observations": "",
            "final_observations": "",
            "peripheral_observations": "",
            "coverage_percent": "",
            "coverage_areas": "",
            "general": {
              "B22": {
                "answer": "OK",
                "justification": ""
              },
              "B23": {
                "answer": "OK",
                "justification": ""
              }
            },
            "peripherals": {
              "B107": {
                "answer": "OK",
                "justification": ""
              },
              "B108": {
                "answer": "OK",
                "justification": ""
              },
              "B109": {
                "answer": "OK",
                "justification": ""
              },
              "B110": {
                "answer": "OK",
                "justification": ""
              },
              "B111": {
                "answer": "OK",
                "justification": ""
              },
              "B112": {
                "answer": "OK",
                "justification": ""
              },
              "B113": {
                "answer": "OK",
                "justification": ""
              },
              "B115": {
                "answer": "OK",
                "justification": ""
              }
            },
            "trials": {
              "B121": {
                "answer": "OK",
                "justification": ""
              },
              "B122": {
                "answer": "OK",
                "justification": ""
              },
              "B123": {
                "answer": "OK",
                "justification": ""
              },
              "B124": {
                "answer": "OK",
                "justification": ""
              },
              "B125": {
                "answer": "OK",
                "justification": ""
              },
              "B126": {
                "answer": "OK",
                "justification": ""
              },
              "B127": {
                "answer": "OK",
                "justification": ""
              }
            },
            "configuration": {
              "conventional": true,
              "addressable": false,
              "repeater": false
            },
            "conventional": [
              {
                "id": "unit-demo-0001",
                "brand": "Marca Fictícia",
                "model": "Modelo Demo",
                "location": "Receção fictícia",
                "total": "4",
                "used": "2",
                "detectors": "12",
                "buttons": "2",
                "sirens": "2",
                "observations": "",
                "checks": {
                  "B44": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B45": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B46": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B47": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B48": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B49": {
                    "answer": "OK",
                    "justification": ""
                  }
                }
              }
            ],
            "addressable": [],
            "repeater": [],
            "photos": [],
            "signatures": {},
            "signature_drafts": {},
            "peripheral_history": []
          }
        ]
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": [
          1
        ],
        "siteStatus": [
          "Em preenchimento"
        ]
      },
      "note": "Indicar percentagem ou áreas testadas."
    },
    {
      "id": "sadi-cobertura-zero",
      "title": "SADI com cobertura 0%",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": false,
          "manutencao": true,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-09-28"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": [
          {
            "id": "site-demo-0001",
            "version": "sadi-2",
            "location": "Local Fictício A",
            "date": "2026-09-28",
            "technician": "Técnico Demonstração A",
            "scie": "",
            "departure": "",
            "period": "monthly",
            "period_other": "",
            "observations": "",
            "final_observations": "",
            "peripheral_observations": "",
            "coverage_percent": "0",
            "coverage_areas": "",
            "general": {
              "B22": {
                "answer": "OK",
                "justification": ""
              },
              "B23": {
                "answer": "OK",
                "justification": ""
              }
            },
            "peripherals": {
              "B107": {
                "answer": "OK",
                "justification": ""
              },
              "B108": {
                "answer": "OK",
                "justification": ""
              },
              "B109": {
                "answer": "OK",
                "justification": ""
              },
              "B110": {
                "answer": "OK",
                "justification": ""
              },
              "B111": {
                "answer": "OK",
                "justification": ""
              },
              "B112": {
                "answer": "OK",
                "justification": ""
              },
              "B113": {
                "answer": "OK",
                "justification": ""
              },
              "B115": {
                "answer": "OK",
                "justification": ""
              }
            },
            "trials": {
              "B121": {
                "answer": "OK",
                "justification": ""
              },
              "B122": {
                "answer": "OK",
                "justification": ""
              },
              "B123": {
                "answer": "OK",
                "justification": ""
              },
              "B124": {
                "answer": "OK",
                "justification": ""
              },
              "B125": {
                "answer": "OK",
                "justification": ""
              },
              "B126": {
                "answer": "OK",
                "justification": ""
              },
              "B127": {
                "answer": "OK",
                "justification": ""
              }
            },
            "configuration": {
              "conventional": true,
              "addressable": false,
              "repeater": false
            },
            "conventional": [
              {
                "id": "unit-demo-0001",
                "brand": "Marca Fictícia",
                "model": "Modelo Demo",
                "location": "Receção fictícia",
                "total": "4",
                "used": "2",
                "detectors": "12",
                "buttons": "2",
                "sirens": "2",
                "observations": "",
                "checks": {
                  "B44": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B45": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B46": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B47": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B48": {
                    "answer": "OK",
                    "justification": ""
                  },
                  "B49": {
                    "answer": "OK",
                    "justification": ""
                  }
                }
              }
            ],
            "addressable": [],
            "repeater": [],
            "photos": [],
            "signatures": {},
            "signature_drafts": {},
            "peripheral_history": []
          }
        ]
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 0,
        "siteErrors": [
          0
        ],
        "siteStatus": [
          "Por assinar"
        ]
      },
      "note": "O modelo atual aceita zero; adequação funcional identificada para decisão no backlog."
    },
    {
      "id": "data-impossivel",
      "title": "Folha com data impossível",
      "document": {
        "document_language": "pt",
        "service_number": "DEMO-0001",
        "customer_name": "Cliente Demonstração",
        "requested_by": "Contacto Demonstração",
        "request_date": "",
        "customer_number": "",
        "nif_number": "",
        "vat_number": "",
        "customer_email": "",
        "customer_phone": "",
        "site_contact": "",
        "site_phone": "",
        "local_store": "Instalação Fictícia",
        "work_number": "0042",
        "contract_number": "",
        "address": "",
        "store_number": "",
        "requested_tasks": "Verificação demonstrativa do sistema.",
        "intervention_report": "Exemplo sintético para desenvolvimento da interface.",
        "customer_signer_name": "Cliente Demonstração",
        "customer_signature_date": "2026-09-28",
        "service_types": {
          "piquete": false,
          "assistencia": true,
          "manutencao": false,
          "formacao": false,
          "colocacao_servico": false,
          "reparacao_oficina": false,
          "garantia": false,
          "instalacao": false
        },
        "equipments": {
          "sadi": true,
          "vss": false,
          "sadco": false,
          "sadir": false,
          "sadei": false,
          "sca": false,
          "eas": false,
          "sadg": false,
          "sch": false,
          "other": false
        },
        "materials_used": false,
        "materials": [
          {
            "ref": "",
            "description": "",
            "qty": ""
          }
        ],
        "technician_records": [
          {
            "technician": "Técnico Demonstração A",
            "start_time": "09:00",
            "end_time": "10:30",
            "total_hours": "1 h 30 min",
            "total_hours_overridden": false,
            "date": "2026-02-30"
          }
        ],
        "client_not_present": false,
        "maintenance_checklists": []
      },
      "expected": {
        "documentMissing": 0,
        "documentInvalid": 1,
        "siteErrors": []
      },
      "note": "Uma data preenchida mas impossível deve gerar erro."
    }
  ]
};
