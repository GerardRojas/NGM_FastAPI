"""
Router para gestion de Material Categories
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client, Client
import os

router = APIRouter(prefix="/material-categories", tags=["material-categories"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[str] = None
    sort_order: Optional[int] = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_categories(include_inactive: bool = False):
    """
    Lista todas las categorias de materiales.
    Por defecto solo muestra activas.
    """
    try:
        query = supabase.table("material_categories").select("*")

        if not include_inactive:
            query = query.eq("is_active", True)

        response = query.order("sort_order").order("name").execute()

        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching categories: {str(e)}")


@router.get("/tree")
async def get_categories_tree():
    """
    Retorna categorias en estructura de arbol (con hijos anidados).
    """
    try:
        response = supabase.table("material_categories").select("*").eq("is_active", True).order("sort_order").order("name").execute()

        categories = response.data or []

        # Construir arbol
        categories_by_id = {c["id"]: {**c, "children": []} for c in categories}
        root_categories = []

        for cat in categories:
            if cat["parent_id"] and cat["parent_id"] in categories_by_id:
                categories_by_id[cat["parent_id"]]["children"].append(categories_by_id[cat["id"]])
            else:
                root_categories.append(categories_by_id[cat["id"]])

        return {"data": root_categories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching categories tree: {str(e)}")


@router.get("/{category_id}")
async def get_category(category_id: str):
    """
    Obtiene una categoria especifica por ID.
    """
    try:
        response = supabase.table("material_categories").select("*").eq("id", category_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Category not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching category: {str(e)}")


@router.post("")
async def create_category(category: CategoryCreate):
    """
    Crea una nueva categoria.
    """
    try:
        # Verificar que no exista una categoria con el mismo nombre
        existing = supabase.table("material_categories").select("id").eq("name", category.name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Category with this name already exists")

        insert_data = {
            "name": category.name,
            "description": category.description,
            "parent_id": category.parent_id,
            "sort_order": category.sort_order or 0,
        }

        response = supabase.table("material_categories").insert(insert_data).execute()

        return {"message": "Category created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating category: {str(e)}")


@router.patch("/{category_id}")
async def update_category(category_id: str, category: CategoryUpdate):
    """
    Actualiza una categoria existente.
    """
    try:
        # Verificar que la categoria exista
        existing = supabase.table("material_categories").select("id").eq("id", category_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Category not found")

        update_data = {}

        if category.name is not None:
            # Verificar que el nombre no este en uso
            name_check = supabase.table("material_categories").select("id").eq("name", category.name).neq("id", category_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Category name already in use")
            update_data["name"] = category.name

        if category.description is not None:
            update_data["description"] = category.description
        if category.parent_id is not None:
            update_data["parent_id"] = category.parent_id
        if category.sort_order is not None:
            update_data["sort_order"] = category.sort_order
        if category.is_active is not None:
            update_data["is_active"] = category.is_active

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("material_categories").update(update_data).eq("id", category_id).execute()

        return {"message": "Category updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating category: {str(e)}")


@router.delete("/{category_id}")
async def delete_category(category_id: str):
    """
    Elimina (soft delete) una categoria.
    """
    try:
        # Verificar que la categoria exista
        existing = supabase.table("material_categories").select("id").eq("id", category_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Category not found")

        # Verificar si hay materiales usando esta categoria
        materials_check = supabase.table("materials").select('"ID"').eq("category_id", category_id).limit(1).execute()

        if materials_check.data and len(materials_check.data) > 0:
            # Soft delete - solo desactivar
            supabase.table("material_categories").update({"is_active": False}).eq("id", category_id).execute()
            return {
                "message": "Category deactivated (has materials assigned)",
                "soft_delete": True
            }

        # Hard delete si no hay materiales
        supabase.table("material_categories").delete().eq("id", category_id).execute()

        return {"message": "Category deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting category: {str(e)}")
