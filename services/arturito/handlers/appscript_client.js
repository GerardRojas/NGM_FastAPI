/**
 * ============================================
 * ARTURITO - Google Chat Bot (Thin Client)
 * ============================================
 * Este script actúa como "mesero" entre Google Chat y el backend NGM.
 * Toda la lógica de interpretación y procesamiento está en el backend.
 *
 * CONFIGURACIÓN:
 * 1. Crea un nuevo proyecto en Google Apps Script
 * 2. Pega este código en Code.gs
 * 3. Configura las propiedades del script (ver abajo)
 * 4. Despliega como Web App
 * 5. Configura el bot en Google Chat API para usar la URL del deployment
 */

// ============================================
// CONFIGURACIÓN
// ============================================

/**
 * URL de tu backend NGM API
 * Configúrala en: Project Settings → Script Properties
 * Key: BACKEND_URL
 * Value: https://tu-api.onrender.com (sin /arturito al final)
 */
function getBackendUrl() {
  const url = PropertiesService.getScriptProperties().getProperty("BACKEND_URL");
  if (!url) {
    throw new Error("BACKEND_URL not configured in Script Properties");
  }
  return url;
}

/**
 * Token de autenticación opcional para el backend
 * Key: BACKEND_TOKEN (opcional)
 */
function getBackendToken() {
  return PropertiesService.getScriptProperties().getProperty("BACKEND_TOKEN") || "";
}

// ============================================
// ENTRY POINTS
// ============================================

/**
 * Maneja eventos POST de Google Chat
 * Este es el entry point principal del bot
 */
function doPost(e) {
  try {
    // Health check si no hay payload
    if (!e || !e.postData || !e.postData.contents) {
      return jsonResponse({ text: "🟢 Arturito endpoint alive." });
    }

    const data = JSON.parse(e.postData.contents || "{}");
    const eventType = (data.type || "").toUpperCase();

    // Extraer información del evento
    const space = data.space || {};
    const message = data.message || {};
    const user = data.user || {};

    const context = {
      space_name: space.displayName || space.name || "default",
      space_id: space.name || "default",
      user_name: user.displayName || user.name || "Usuario",
      user_email: user.email || "",
      thread_id: message.thread ? message.thread.name : null,
    };

    // ========================================
    // EVENTO: Bot agregado al espacio
    // ========================================
    if (eventType === "ADDED_TO_SPACE") {
      return jsonResponse({
        text: `🤖 ¡Hola ${context.user_name}! Soy Arturito y estoy listo para ayudar.\n\nEscribe \`/help\` para ver qué puedo hacer.`
      });
    }

    // ========================================
    // EVENTO: Bot removido del espacio
    // ========================================
    if (eventType === "REMOVED_FROM_SPACE") {
      console.log(`Bot removed from space: ${context.space_name}`);
      return jsonResponse({ text: "" });
    }

    // ========================================
    // EVENTO: Mensaje recibido
    // ========================================
    if (eventType === "MESSAGE") {
      const text = (message.argumentText || message.text || "").trim();

      // Detectar si fue mención directa
      const annotations = message.annotations || [];
      const isMention = annotations.some(a => a.type === "USER_MENTION");

      // Detectar slash commands de Google Chat
      const slashCommand = data.message?.slashCommand;
      if (slashCommand) {
        return handleSlashCommand(slashCommand, text, context);
      }

      // Enviar mensaje al backend
      return handleMessage(text, context, isMention);
    }

    // Evento no manejado
    return jsonResponse({ text: "" });

  } catch (error) {
    console.error("Error in doPost:", error);
    return jsonResponse({
      text: "⚠️ Error interno. Por favor intenta de nuevo."
    });
  }
}

/**
 * Health check endpoint (GET)
 */
function doGet(e) {
  return ContentService
    .createTextOutput("🟢 Arturito Google Chat Bot is running.")
    .setMimeType(ContentService.MimeType.TEXT);
}

// ============================================
// HANDLERS
// ============================================

/**
 * Envía el mensaje al backend y retorna la respuesta
 */
function handleMessage(text, context, isMention) {
  if (!text) {
    return jsonResponse({
      text: "No recibí ningún mensaje. ¿En qué puedo ayudarte?"
    });
  }

  try {
    const backendUrl = getBackendUrl();
    const endpoint = `${backendUrl}/arturito/message`;

    const payload = {
      text: text,
      user_name: context.user_name,
      user_email: context.user_email,
      space_name: context.space_name,
      space_id: context.space_id,
      thread_id: context.thread_id,
      is_mention: isMention
    };

    console.log(`[ARTURITO] Sending to backend: ${JSON.stringify(payload)}`);

    const response = callBackend(endpoint, payload);

    console.log(`[ARTURITO] Backend response: ${JSON.stringify(response)}`);

    return formatResponse(response);

  } catch (error) {
    console.error("[ARTURITO] Error calling backend:", error);
    return jsonResponse({
      text: `⚠️ No pude conectar con el servidor: ${error.message}`
    });
  }
}

/**
 * Maneja slash commands de Google Chat
 */
function handleSlashCommand(slashCommand, fullText, context) {
  const commandId = slashCommand.commandId;

  // Mapeo de command IDs a nombres (configúralo según tu bot)
  const commandMap = {
    1: "ping",
    2: "budgetvsactuals",
    3: "help",
    4: "sarcasmo"
  };

  const commandName = commandMap[commandId] || "unknown";

  // Extraer argumentos (texto después del comando)
  const args = fullText.replace(/^\/\w+\s*/, "").trim();

  try {
    const backendUrl = getBackendUrl();
    const endpoint = `${backendUrl}/arturito/slash`;

    const payload = {
      command: commandName,
      args: args,
      user_name: context.user_name,
      user_email: context.user_email,
      space_name: context.space_name,
      space_id: context.space_id
    };

    console.log(`[ARTURITO] Slash command: ${JSON.stringify(payload)}`);

    const response = callBackend(endpoint, payload);

    return formatResponse(response);

  } catch (error) {
    console.error("[ARTURITO] Error with slash command:", error);
    return jsonResponse({
      text: `⚠️ Error ejecutando /${commandName}: ${error.message}`
    });
  }
}

// ============================================
// BACKEND COMMUNICATION
// ============================================

/**
 * Llama al backend NGM API
 */
function callBackend(url, payload) {
  const token = getBackendToken();

  const options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    headers: {}
  };

  // Agregar token si está configurado
  if (token) {
    options.headers["Authorization"] = `Bearer ${token}`;
  }

  const response = UrlFetchApp.fetch(url, options);
  const code = response.getResponseCode();
  const body = response.getContentText();

  if (code !== 200) {
    console.error(`[ARTURITO] Backend error ${code}: ${body}`);
    throw new Error(`HTTP ${code}`);
  }

  try {
    return JSON.parse(body);
  } catch (e) {
    console.error("[ARTURITO] Invalid JSON response:", body);
    throw new Error("Invalid response from backend");
  }
}

// ============================================
// RESPONSE FORMATTING
// ============================================

/**
 * Formatea la respuesta del backend para Google Chat
 */
function formatResponse(backendResponse) {
  // Si el backend retorna una card
  if (backendResponse.card) {
    return jsonResponse({
      cardsV2: [formatCard(backendResponse)]
    });
  }

  // Si hay datos con PDF URL, crear card con botón
  if (backendResponse.data && backendResponse.data.pdf_url) {
    return jsonResponse({
      text: backendResponse.text,
      cardsV2: [createPdfCard(backendResponse)]
    });
  }

  // Respuesta de texto simple
  return jsonResponse({
    text: backendResponse.text || "✅ Listo."
  });
}

/**
 * Crea una card para mostrar link al PDF
 */
function createPdfCard(response) {
  const data = response.data || {};

  return {
    cardId: "bva-report-card",
    card: {
      header: {
        title: "📊 Budget vs Actuals Report",
        subtitle: data.project_name || "Reporte generado"
      },
      sections: [
        {
          widgets: [
            {
              decoratedText: {
                topLabel: "Budget",
                text: `$${formatNumber(data.totals?.budget || 0)}`
              }
            },
            {
              decoratedText: {
                topLabel: "Actual",
                text: `$${formatNumber(data.totals?.actual || 0)}`
              }
            },
            {
              decoratedText: {
                topLabel: "Balance",
                text: `$${formatNumber(data.totals?.balance || 0)}`
              }
            },
            {
              buttonList: {
                buttons: [
                  {
                    text: "📄 Ver Reporte PDF",
                    onClick: {
                      openLink: {
                        url: data.pdf_url
                      }
                    }
                  }
                ]
              }
            }
          ]
        }
      ]
    }
  };
}

/**
 * Formatea una card genérica del backend
 */
function formatCard(backendResponse) {
  const card = backendResponse.card;

  return {
    cardId: card.id || "generic-card",
    card: {
      header: {
        title: card.title || "",
        subtitle: card.subtitle || ""
      },
      sections: [
        {
          widgets: card.button ? [
            {
              buttonList: {
                buttons: [
                  {
                    text: card.button.text,
                    onClick: {
                      openLink: {
                        url: card.button.url
                      }
                    }
                  }
                ]
              }
            }
          ] : []
        }
      ]
    }
  };
}

// ============================================
// UTILITIES
// ============================================

/**
 * Crea respuesta JSON para Google Chat
 */
function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Formatea números con comas
 */
function formatNumber(num) {
  return parseFloat(num || 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

// ============================================
// SETUP & TEST FUNCTIONS
// ============================================

/**
 * Función de prueba para verificar conexión con backend
 * Ejecútala desde el editor de Apps Script
 */
function testBackendConnection() {
  try {
    const backendUrl = getBackendUrl();
    const endpoint = `${backendUrl}/arturito/health`;

    const response = UrlFetchApp.fetch(endpoint, {
      method: "get",
      muteHttpExceptions: true
    });

    const code = response.getResponseCode();
    const body = response.getContentText();

    console.log(`Backend health check: ${code}`);
    console.log(`Response: ${body}`);

    if (code === 200) {
      console.log("✅ Backend connection successful!");
    } else {
      console.log("❌ Backend returned error");
    }

    return body;

  } catch (error) {
    console.error("❌ Failed to connect to backend:", error);
    return null;
  }
}

/**
 * Función de prueba para simular un mensaje
 */
function testMessage() {
  const fakeEvent = {
    postData: {
      contents: JSON.stringify({
        type: "MESSAGE",
        space: { displayName: "Test Space", name: "spaces/test" },
        user: { displayName: "Test User", email: "test@example.com" },
        message: { text: "BVA de Del Rio" }
      })
    }
  };

  const result = doPost(fakeEvent);
  console.log("Response:", result.getContent());
}

/**
 * Configura las propiedades del script
 * Ejecuta esto UNA VEZ para configurar el backend URL
 */
function setupScriptProperties() {
  const props = PropertiesService.getScriptProperties();

  // ⚠️ CAMBIA ESTA URL POR LA DE TU BACKEND
  props.setProperty("BACKEND_URL", "https://ngm-fastapi.onrender.com");

  // Opcional: token de autenticación
  // props.setProperty("BACKEND_TOKEN", "tu-token-secreto");

  console.log("✅ Script properties configured!");
  console.log("BACKEND_URL:", props.getProperty("BACKEND_URL"));
}
