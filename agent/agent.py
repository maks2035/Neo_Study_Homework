import json
import re
from datetime import datetime
from typing import Optional
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, ValidationError
from openai import OpenAI

STORAGE_FILE = Path(__file__).parent / "reminders.json" 

NEO_MODEL_GPT = "gpt-oss-120b"
NEO_URL = "https://litellm.happyhub.ovh/v1"


def load_api_key(path_to_key):
   # Загрузка ключа openroutera
   key_path = Path(__file__).resolve().parent.parent.parent / path_to_key
   with open(key_path, "r") as f:
      return f.read().strip()

# === Pydantic-модели ===

class Reminder(BaseModel):
   """Структура напоминания в хранилище."""
   id: str
   title: str
   event_date: str  # ISO format: "2026-04-20"
   description: str = ""
   created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CreateReminderInput(BaseModel):
   """Входные данные для создания напоминания."""
   title: str = Field(..., min_length=1)
   event_date: str
   description: str = ""
   
   @field_validator('event_date', mode='before')
   @classmethod
   def parse_date(cls, v):
      # Нормализует ДД.ММ.ГГГГ → ГГГГ-ММ-ДД
      if v is None:
         return None
      v_str = str(v).strip()
      if not v_str:
         return None
      if re.match(r'^\d{2}\.\d{2}\.\d{4}$', v_str):
         d, m, y = v_str.split('.')
         return f"{y}-{m}-{d}"
      return v_str


class UpdateReminderInput(BaseModel):
   """Входные данные для обновления: только изменяемые поля."""
   reminder_id: str
   title: Optional[str] = None
   event_date: Optional[str] = None
   description: Optional[str] = None
   
   @field_validator('event_date', mode='before')
   @classmethod
   def parse_date(cls, v):
      # Дублируем валидатор
      if v is None:
         return None
      v_str = str(v).strip()
      if not v_str:
         return None
      if re.match(r'^\d{2}\.\d{2}\.\d{4}$', v_str):
         d, m, y = v_str.split('.')
         return f"{y}-{m}-{d}"
      return v_str


class DeleteReminderInput(BaseModel):
   """Входные данные для удаления."""
   reminder_id: str


# === Инструменты (логика) ===

def create_reminder(**kwargs):
   """Создаёт новое напоминание."""
   input = CreateReminderInput(**kwargs)
   reminders = _load_reminders()
   new_id = f"rem_{len(reminders)+1}_{datetime.now().strftime('%H%M%S')}"
   reminder = Reminder(id=new_id, **input.model_dump())
   reminders.append(reminder)
   _save_reminders(reminders)
   return {"status": "created", "id": new_id}


def list_reminders(filter = "all"):
   """
   Возвращает список с фильтрацией.
   ВАЖНО: фильтрация работает только относительно ТЕКУЩЕЙ даты (datetime.now()):
    - filter="all"      : все напоминания
    - filter="upcoming" : только с event_date >= сегодня
    - filter="past"     : только с event_date < сегодня
   """
   reminders = _load_reminders()
   now = datetime.now().date()
   result = []
   for r in reminders:
      event_date = datetime.fromisoformat(r.event_date).date()
      if filter == "upcoming" and event_date < now:
         continue
      if filter == "past" and event_date >= now:
         continue
      result.append(r.model_dump(mode='json'))
   return {"count": len(result), "reminders": result}


def update_reminder(**kwargs):
   """Обновляет поля по ID."""
   input = UpdateReminderInput(**kwargs)
   reminders = _load_reminders()
   for i, r in enumerate(reminders):
      if r.id == input.reminder_id:
         update_data = {k: v for k, v in input.model_dump().items() 
                        if v is not None and k != 'reminder_id'}
         updated = r.model_dump()
         updated.update(update_data)
         reminders[i] = Reminder.model_validate(updated)
         _save_reminders(reminders)
         return {"status": "updated", "id": input.reminder_id}
   return {"status": "not_found"}


def delete_reminder(**kwargs): 
   """Удаляет напоминание по ID."""
   input = DeleteReminderInput(**kwargs)
   reminders = _load_reminders()
   before = len(reminders)
   reminders = [r for r in reminders if r.id != input.reminder_id]
   if len(reminders) < before:
      _save_reminders(reminders)
      return {"status": "deleted", "id": input.reminder_id}
   return {"status": "not_found"}


# === JSON-хранилище ===

def _load_reminders():
   """Читает напоминания из файла с авто-восстановлением при повреждении."""
   if not STORAGE_FILE.exists():
      return []
   try:
      content = STORAGE_FILE.read_text(encoding="utf-8").strip()
      if not content:
         return []
      data = json.loads(content)
      return [Reminder.model_validate(r) for r in data]
   except (json.JSONDecodeError, ValueError):
      _save_reminders([])
      return []


def _save_reminders(reminders):
   """Сохраняет напоминания в файл."""
   STORAGE_FILE.write_text(
      json.dumps([r.model_dump(mode='json') for r in reminders], 
               ensure_ascii=False, indent=2),
      encoding="utf-8"
   )


# === Реестр инструментов для Function Calling ===

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Create a new reminder with title and event date",
            "parameters": CreateReminderInput.model_json_schema()
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "list_reminders",
            "description": "List reminders. filter: 'all', 'upcoming', or 'past'",
            "parameters": {"type": "object", "properties": {
                "filter": {"type": "string", "enum": ["all", "upcoming", "past"], "default": "all"}
            }}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_reminder", 
            "description": "Update existing reminder by ID. Only provided fields will be changed",
            "parameters": UpdateReminderInput.model_json_schema()
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": "Delete reminder by ID",
            "parameters": DeleteReminderInput.model_json_schema()
        }
    }
]

FUNCTION_MAP = {
    "create_reminder": create_reminder, 
    "list_reminders": list_reminders, 
    "update_reminder": update_reminder, 
    "delete_reminder": delete_reminder
}

SYSTEM_PROMPT = """You manage reminders. 
Use tools. 
Answer in user's language."""


def run_agent(user_message, client, model, max_steps=3):
   """Основной цикл агента с формированием чистых ответов после вызова инструментов."""
   
   messages = [
      {"role": "system", "content": SYSTEM_PROMPT},
      {"role": "user", "content": user_message}
   ]
   
   for _ in range(max_steps):
      try:
         response = client.chat.completions.create(
               model=model,
               messages=messages,
               tools=TOOLS,
               tool_choice="auto"
         )
         
         if response is None or not hasattr(response, "choices") or not response.choices:
               return "Sorry, I encountered an error. Please try again."
         
         msg = response.choices[0].message
         
      except Exception as e:
         return f"Error: {str(e)[:150]}"
      
      if not msg.tool_calls:
         return msg.content
      
      tool_responses = []
      for tool_call in msg.tool_calls:
         func_name = tool_call.function.name
         
         # Безопасный парсинг аргументов
         raw_args = tool_call.function.arguments or "{}"
         try:
            args = json.loads(raw_args)
         except json.JSONDecodeError:
            print(f"[WARNING] Invalid JSON for {func_name}: '{raw_args[:50]}'")
            args = {}
         
         # Выполнение инструмента с обработкой ошибок
         try:
            result = FUNCTION_MAP[func_name](**args)
         except ValidationError as e:
            result = {"status": "error", "message": "Не удалось выполнить: проверьте формат данных"}
         except Exception as e:
            result = {"status": "error", "message": "Внутренняя ошибка при выполнении операции"}
         
         # Формируем читаемый ответ для пользователя
         if func_name == "create_reminder":
               if result["status"] == "created":
                  tool_responses.append(f"Напоминание создано: {result['id']}")
               elif result["status"] == "error":
                  tool_responses.append(f"Ошибка: {result.get('message', 'Неизвестная ошибка')}")
               else:
                  tool_responses.append(f"Ошибка: {result}")
         
         elif func_name == "update_reminder":
               if result["status"] == "updated":
                  tool_responses.append(f"Напоминание обновлено: {result['id']}")
               elif result["status"] == "not_found":
                  tool_responses.append("Напоминание не найдено")
               elif result["status"] == "error":
                  tool_responses.append(f"Ошибка: {result.get('message', 'Неизвестная ошибка')}")
               else:
                  tool_responses.append(f"Ошибка: {result}")
         
         elif func_name == "delete_reminder":
               if result["status"] == "deleted":
                  tool_responses.append(f"Напоминание удалено: {result['id']}")
               elif result["status"] == "not_found":
                  tool_responses.append("Напоминание не найдено")
               elif result["status"] == "error":
                  tool_responses.append(f"Ошибка: {result.get('message', 'Неизвестная ошибка')}")
               else:
                  tool_responses.append(f"Ошибка: {result}")
         
         elif func_name == "list_reminders":
               if result.get("status") == "error":
                  tool_responses.append(f"Ошибка: {result.get('message', 'Неизвестная ошибка')}")
               elif result["count"] == 0:
                  tool_responses.append("Нет напоминаний")
               else:
                  items = []
                  for r in result["reminders"]:
                     line = f"{r['id']}: {r['title']} ({r['event_date']})"
                     items.append(line)
                  tool_responses.append("Список записей:\n" + "\n".join(items))
         
         messages.append({
               "role": "tool",
               "tool_call_id": tool_call.id,
               "name": func_name,
               "content": json.dumps(result, ensure_ascii=False)
         })
      
      return "\n".join(tool_responses)
   
   return "Max steps reached. Please rephrase your request."


if __name__ == "__main__":
   KEY = load_api_key("NEO_KEY.txt")
   client = OpenAI(api_key=KEY, base_url=NEO_URL)

   print("Reminder Agent started. Type 'exit' to quit.\n")
   while True:
      user_input = input("You: ").strip()
      if user_input.lower() in ["exit", "quit"]:
         break
      if not user_input:
         continue
      
      response = run_agent(user_input, client, NEO_MODEL_GPT)
      print(f"Agent: {response}\n")