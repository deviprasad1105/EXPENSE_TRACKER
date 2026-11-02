from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, or_, select, true, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Account, AccountTransfer, Expense, Fund, User
from ..schemas import AccountIn, AccountOut, TransferIn, TransferOut
from ..services.accounts import balances
from ..timeutil import today_local

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _load(db: Session, viewer: User, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    resolve_target(db, viewer, account.user_id, write=True)
    return account


def _make_primary(db: Session, account: Account):
    db.execute(update(Account).where(Account.user_id == account.user_id, Account.id != account.id).values(is_primary=False))
    account.is_primary = True


def _out(db: Session, account: Account) -> AccountOut:
    item = AccountOut.model_validate(account)
    for k, v in balances(db, account.user_id, today_local().replace(day=1)).get(account.id, {}).items():
        setattr(item, k, v)
    return item


def _check_name(db: Session, user_id: int, name: str, account_id: int | None = None):
    clash = db.scalar(
        select(Account.id).where(Account.user_id == user_id, func.lower(Account.name) == name.lower(), Account.id != (account_id or 0))
    )
    if clash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You already have an account called '{name}'")


@router.get("", response_model=list[AccountOut])
def list_accounts(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Accounts with their balance: opening balance + money received - spent + transfers in - transfers out."""
    target = resolve_target(db, viewer, user_id)
    totals = balances(db, target.user.id, today_local().replace(day=1))
    accounts = db.scalars(
        select(Account).where(Account.user_id == target.user.id).order_by(Account.is_primary.desc(), Account.is_active.desc(), Account.name)
    ).all()
    out = []
    for a in accounts:
        item = AccountOut.model_validate(a)
        for k, v in totals.get(a.id, {}).items():
            setattr(item, k, v)
        out.append(item)
    return out


@router.post("", response_model=AccountOut, status_code=201)
def add_account(data: AccountIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    _check_name(db, target.user.id, data.name)
    account = Account(**data.model_dump(exclude={"is_primary"}), user_id=target.user.id)
    db.add(account)
    db.flush()
    # The first account becomes the primary one
    has_primary = db.scalar(select(Account.id).where(Account.user_id == target.user.id, Account.is_primary == true()))
    if data.is_primary or not has_primary:
        _make_primary(db, account)
    db.commit()
    return _out(db, account)


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = _load(db, viewer, account_id)
    _check_name(db, account.user_id, data.name, account.id)
    for key, value in data.model_dump(exclude={"is_primary"}).items():
        setattr(account, key, value)
    if data.is_primary and data.is_active:
        _make_primary(db, account)
    elif not data.is_active:
        account.is_primary = False
    db.commit()
    return _out(db, account)


@router.post("/{account_id}/primary", response_model=AccountOut)
def set_primary(account_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Expenses without an account chosen are paid from the primary account."""
    account = _load(db, viewer, account_id)
    if not account.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A closed account can't be the primary one")
    _make_primary(db, account)
    db.commit()
    return _out(db, account)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Expenses and money received stay, with no account; transfers to or from it are deleted."""
    account = _load(db, viewer, account_id)
    db.execute(update(Expense).where(Expense.account_id == account.id).values(account_id=None))
    db.execute(update(Fund).where(Fund.account_id == account.id).values(account_id=None))
    db.execute(delete(AccountTransfer).where(or_(AccountTransfer.from_account_id == account.id, AccountTransfer.to_account_id == account.id)))
    db.delete(account)
    db.commit()


# ---------- transfers between own accounts ----------
@router.get("/transfers", response_model=list[TransferOut])
def list_transfers(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    return db.scalars(
        select(AccountTransfer).where(AccountTransfer.user_id == target.user.id)
        .order_by(AccountTransfer.transfer_date.desc(), AccountTransfer.id.desc()).limit(200)
    ).all()


@router.post("/transfers", response_model=TransferOut, status_code=201)
def add_transfer(data: TransferIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Money moved from one of your accounts to another, e.g. salary account → spending account."""
    target = resolve_target(db, viewer, user_id, write=True)
    if data.from_account_id == data.to_account_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose two different accounts")
    for aid in (data.from_account_id, data.to_account_id):
        a = db.get(Account, aid)
        if not a or a.user_id != target.user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid account")
    transfer = AccountTransfer(**data.model_dump(), user_id=target.user.id)
    db.add(transfer)
    db.commit()
    return transfer


@router.delete("/transfers/{transfer_id}", status_code=204)
def delete_transfer(transfer_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transfer = db.get(AccountTransfer, transfer_id)
    if not transfer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer not found")
    resolve_target(db, viewer, transfer.user_id, write=True)
    db.delete(transfer)
    db.commit()
